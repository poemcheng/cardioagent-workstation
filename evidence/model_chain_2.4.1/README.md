# 2.4.1 development verification

- `software_tests.log`: 227 Python regressions passed on Linux / CPython 3.12.14. Ten tests cover the added model-chain work.
- `preparation_dom.json`: seven existing preparation workspace checks, using the actual Python Flask transport. No browser pixel claims.
- `renderer.json`: seven renderer logic checks with recording doubles.
- `missing_model_run/`: actual command execution in this environment, with no registered CAC weights and no Lemonade service. The requested scope correctly failed, and no replacement chain or approval was generated. The all-zero expected SHA256 is an intentional unavailable-model probe, never a real model identity.

The added tests execute an untrained ONNX contract graph in ONNX Runtime and a real loopback HTTP protocol fixture. The fixture's training-declaration flag is temporarily set in selected isolated tests to exercise the acceptance contract; that does not train it or turn it into clinical weights. None of those test graphs, model manifests or mock service responses are distributed as model results.

The missing-model report identifies the base checkout commit; its `source_files_sha256` records the exact working files used before this change was committed. Public browser observation and Windows CI results are separate executions. None of this evidence verifies PN54/NPU execution, original ST-CAC accuracy, PyTorch equivalence or clinical time savings.
