# Preserved workspace integration

The remote repository contained newer identity, medication, private-media and structured-intake safeguards than the local consultation build. Publishing the local app over those modules would remove those safeguards. The local build is therefore published as `smartsehat_workspace`, with its own `tests_workspace` suite. The existing `smartsehat` app and tests remain unchanged.

Run `python -m streamlit run smartsehat_workspace/app.py --server.address 127.0.0.1` for the completed consultation/doctor/Dawa/dashboard workflow. Its own encrypted consultation store is separate from the original case store. The two apps are not a unified authentication boundary. A future merge must port and test access controls, medication contracts, media handling and transcript provenance explicitly; copying one entry point over the other is not acceptable.

Generated encrypted speech recordings/caches, uploads, keys, local databases, build tooling, presentation archives and machine-specific shortcuts are excluded. Recreate optional speech assets locally using the documented helper. The saved fictional walkthrough demonstrates the local build; live model output can differ. Original provider configuration on GitHub is preserved. Provider names and availability must be checked against your account; no keys are included.
