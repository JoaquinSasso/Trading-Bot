import sys
print("Python Executable:", sys.executable)
print("Python Version:", sys.version)

try:
    import torch
    print("PyTorch Version:", torch.__version__)
    print("CUDA Available:", torch.cuda.is_available())
    if torch.cuda.is_available():
        print("Device Name:", torch.cuda.get_device_name(0))
        print("Device Memory (GB):", round(torch.cuda.get_device_properties(0).total_memory / 1e9, 2))
except ImportError as e:
    print("PyTorch NOT installed:", e)

try:
    import transformers
    print("Transformers Version:", transformers.__version__)
except ImportError as e:
    print("Transformers NOT installed:", e)

try:
    import onnxruntime as ort
    print("ONNX Runtime Version:", ort.__version__)
except ImportError as e:
    print("ONNX Runtime NOT installed:", e)

try:
    import pandas as pd
    print("Pandas Version:", pd.__version__)
except ImportError as e:
    print("Pandas NOT installed:", e)
