# Vendored: LeRobot `port_droid.py`

`port_droid.py` is Hugging Face's DROID RLDS → LeRobot converter, copied byte for byte
from the LeRobot repository so that our experiments run exactly the transformation
Hugging Face's [porting guide](https://huggingface.co/docs/lerobot/porting_datasets_v3)
uses. Do not edit it: `src/cstore/functions/droid_rlds_to_lerobot.py` refuses to run
it if its SHA-256 changes (and `tests/test_core.py` checks the same).

| | |
| --- | --- |
| Source | https://github.com/huggingface/lerobot/blob/v0.6.1/examples/port_datasets/port_droid.py |
| Tag / commit | `v0.6.1` / `7e241bd630a3719a56157a497ce5d08f244784f1` |
| SHA-256 | `3af72c2ca6711cc7a1fd2c1774fda9ad2862a025e029e80a4051bab0c1312b71` |
| License | Apache-2.0 (header in the file; full text at https://github.com/huggingface/lerobot/blob/v0.6.1/LICENSE) |

It needs the `lerobot` package of the same version (0.6.1), which the worker image pins.
