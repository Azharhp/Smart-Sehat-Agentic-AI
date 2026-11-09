"""Focused extraction boundary, upload format and Dawa form regression checks."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import patch
import json

from PIL import Image
from streamlit.testing.v1 import AppTest
from smartsehat_workspace.medicine_vision import parse_medicine_image, vision_failure_message


class MedicineVisionTests(TestCase):
    def setUp(self):
        from smartsehat_workspace import audit
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        db = str(Path(self.temp.name) / "audit.db")
        target = patch.object(audit, "DB_PATH", db)
        target.start()
        self.addCleanup(target.stop)
        audit.init_db()

    def test_response_shapes_and_strength_boundaries(self):
        parsed = parse_medicine_image(json.dumps({"medicines": [
            {"name": "Paracetamol", "strength": "500 mg"},
            {"name": "Multivitamin syrup", "strength": "2 spoons daily"}]}))
        self.assertEqual(parsed["items"][0]["strength"], "500 mg")
        self.assertEqual(parsed["items"][1]["strength"], "unknown")
        self.assertEqual(parsed["items"][0]["formulation"], "unknown")
        self.assertEqual(parse_medicine_image('{"items":[{"name":"Panadol"}]}')["names"], ["Panadol"])
        self.assertEqual(parse_medicine_image('{"medicines":[]}')["items"], [])
        with self.assertRaises(ValueError):
            parse_medicine_image('{"medicines":[{"name":true}]}')
        with self.assertRaises(ValueError):
            parse_medicine_image('unreadable')

    def test_uploaded_png_keeps_actual_mime_and_unique_content(self):
        from smartsehat_workspace.app import _save_dawa_image
        with TemporaryDirectory() as folder, patch('smartsehat_workspace.app.UPLOAD_DIR', Path(folder)):
            data = BytesIO(); Image.new('RGB', (16, 16), 'white').save(data, format='PNG')
            first = Path(_save_dawa_image(SimpleNamespace(getvalue=lambda:data.getvalue(), name='misnamed.jpg')))
            self.assertEqual(first.suffix, '.png')
            self.assertEqual(first.read_bytes(), data.getvalue())
            other = BytesIO(); Image.new('RGB', (16, 16), 'black').save(other, format='JPEG')
            second = Path(_save_dawa_image(SimpleNamespace(getvalue=lambda:other.getvalue(), name='misnamed.png')))
            self.assertEqual(second.suffix, '.jpg')
            self.assertNotEqual(first, second)
            with self.assertRaises(ValueError):
                _save_dawa_image(SimpleNamespace(getvalue=lambda:b'not an image'))

    def test_network_errors_are_actionable_and_do_not_repeat_or_leak(self):
        from smartsehat_workspace import dawa_graph
        router = SimpleNamespace(route_vision=lambda *a: (_ for _ in ()).throw(PermissionError('WinError 10013 token=private-secret')))
        with patch.object(dawa_graph, '_router', return_value=router) as factory, patch.object(dawa_graph.audit, 'log_event') as log:
            result = dawa_graph.extract_node({'case_id':'TEST-VISION-ERROR','rx_image':'fake.jpg'})
        self.assertEqual(factory.call_count, 1)
        self.assertIn('Windows blocked', result['degraded'])
        self.assertNotIn('private-secret', str(log.call_args_list))
        self.assertIn('in time', vision_failure_message(TimeoutError('timeout')))

    def test_add_rows_persist_and_new_check_does_not_reuse_old_names(self):
        source = "from smartsehat_workspace.app import _dawa_tab\n_dawa_tab('TEST-WORKER')\n"
        app = AppTest.from_string(source, default_timeout=20)
        app.session_state['dawa_case_id'] = 'OLD'
        app.session_state['dawa_thread_id'] = 'OLD'
        app.session_state['dawa_stage'] = 'confirm'
        app.session_state['dawa_gate'] = {'gate':'dawa_confirm','medicines':[{'name':'Panadol','strength':'500 mg'}], 'provider':'test'}
        app.run()
        app.text_input(key='dawa_m_OLD_0').set_value('Edited Panadol')
        next(b for b in app.button if b.label=='+ add row').click().run()
        self.assertEqual(app.text_input(key='dawa_m_OLD_0').value,'Edited Panadol')
        self.assertEqual(app.text_input(key='dawa_m_OLD_1').value,'')
        gate = SimpleNamespace(value={'gate':'dawa_confirm','medicines':[], 'degraded':'no_rx_image'})
        with patch('smartsehat_workspace.dawa_graph.run_dawa_case',return_value=('NEW','NEW',{'__interrupt__':[gate]})):
            next(b for b in app.button if b.label=='Enter medicines manually without a photo').click().run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(app.text_input(key='dawa_m_NEW_0').value,'')
        self.assertTrue(next(b for b in app.button if b.label=='Confirm & check').disabled)


if __name__=='__main__':
    main()
