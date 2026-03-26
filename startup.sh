#!/bin/bash
echo "=== STARTUP DIAGNOSTIC ==="
python -c "
import traceback, sys
try:
    import main
    print('main import OK')
except Exception as e:
    traceback.print_exc()
    sys.exit(1)
"
echo "=== STARTING SERVER ==="
uvicorn main:app --host 0.0.0.0 --port $PORT
