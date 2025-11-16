#!/bin/bash
# Quick test script for server_v2 (Rust-powered)

echo "Testing server_v2 (Rust-powered) on port 9923..."

# Run test client pointing to port 9923
python3 -c "
import sys
sys.path.insert(0, '.')
exec(open('scripts/test_audio_streaming_receive.py').read().replace('9922', '9923'))
"
