# Dawa image reading

The observed failures were Windows socket error 10013 from every configured vision provider. The local server had inherited restricted network access. The configured Qwen service successfully read the bundled fictional prescription when run with network access. The local server was then restarted with network access. No provider credentials or model configuration were changed.

## Use

1. Refresh the app and select **Dawa Check**.
2. Upload a JPG or PNG and click **Extract medicines**, or use **Read bundled fictional prescription** for the project's authored sample.
3. Wait for the editable draft. Check each product name and printed strength against the image.
4. Correct entries, use **+ add row** if required, enter the patient context, then click **Confirm & check**.

**Enter medicines manually without a photo** remains available if a service cannot read the image. In the consultation's medicine step, turn **Use live medicine vision** on for a new photo. With it off, only a saved result for that exact image can be read.

## Fixes

- Uploads preserve their actual JPG/PNG format and use content-specific filenames, instead of saving every image as `dawa_rx.jpg`.
- Vision calls in the standalone Dawa workflow use the existing bounded-call mechanism, with a default 25-second UI wait and an explicit failure category.
- Both medicine image routes accept structured `names`, `items` and legacy `medicines` response shapes and reject invalid names.
- A reported schedule or amount such as `2 spoons daily` stays out of the product-strength field.
- A worker must still confirm the draft before medicine checking. No generated name, strength or instruction automatically becomes a confirmed patient fact.
- Added rows persist, and widget keys are scoped to each Dawa case so a new image cannot silently reuse the previous image's names.
- Failure messages distinguish Windows network blocking, timeout, service access, quota and invalid responses without displaying raw provider errors or credentials.
- Provider indicators now say **configured**, since having a key does not prove a working service connection.

Run the launcher from Windows when live service access is needed. A server started inside a restricted agent process may inherit that process's network restrictions.

Focused checks: 4 vision/upload/form regression tests passed, and the existing 24 offline Dawa workflow checks passed. A live configured-provider probe read the bundled sample. Live UI evidence is recorded separately in `finale/dawa-vision-checks.json` when complete.
