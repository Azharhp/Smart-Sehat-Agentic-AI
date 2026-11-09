// Offline fixed-prompt synthesis only. eSpeak NG is GPL-3.0; see the pinned
// package's COPYING and https://github.com/echogarden-project/espeak-ng-emscripten.
import create from '@echogarden/espeak-ng-emscripten';
import {readFile, writeFile} from 'node:fs/promises';
import {resolve, join} from 'node:path';

const [manifest, output, language] = process.argv.slice(2);
const code = {Urdu:'ur', English:'en'}[language];
if (!manifest || !output || !code) throw new Error('Supply manifest, output directory and Urdu/English.');
const engine = await create();
const worker = new engine.eSpeakNGWorker();
const voice = worker.list_voices().find(v => v.languages.some(lang => lang.name === code));
if (!voice) throw new Error('No matching offline voice. A different language will not be substituted.');
worker.set_voice(voice.name, code);
worker.set_rate(145);
const rows = JSON.parse(await readFile(manifest, 'utf8'));
if (!Array.isArray(rows) || rows.length > 200) throw new Error('Invalid fixed-prompt manifest.');
for (const row of rows) {
  if (!/^[a-f0-9]{64}$/.test(row.id) || typeof row.text !== 'string' || !row.text.trim() || row.text.length > 5000) throw new Error('Invalid fixed prompt.');
  const parts = [];
  worker.synthesize(row.text, samples => { if(samples.length) parts.push(Int16Array.from(samples)); return false; });
  const sampleCount = parts.reduce((sum, part) => sum + part.length, 0);
  const rate = worker.get_samplerate();
  if (!sampleCount || !Number.isInteger(rate) || rate < 8000 || sampleCount * 2 > 20 * 1024 * 1024) throw new Error('Unsupported generated audio.');
  const wav = Buffer.alloc(44 + sampleCount * 2);
  wav.write('RIFF',0);wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);
  wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(rate,24);wav.writeUInt32LE(rate*2,28);
  wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(sampleCount*2,40);
  let offset=44;for(const part of parts)for(const sample of part){wav.writeInt16LE(sample,offset);offset+=2;}
  await writeFile(join(resolve(output),row.id+'.wav'),wav);
}
console.log(`Prepared ${rows.length} exact-text ${language} fixed prompts using eSpeak NG ${voice.name}. No network; pronunciation and clinical review pending.`);
