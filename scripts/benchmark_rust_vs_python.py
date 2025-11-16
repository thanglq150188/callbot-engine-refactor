#!/usr/bin/env python3
"""
Benchmark script to compare Rust vs Python audio processing performance
"""

import time
import base64
import statistics
import json
from typing import List, Dict

try:
    import callbot_rust
    RUST_AVAILABLE = True
except ImportError:
    RUST_AVAILABLE = False
    print("⚠️  Rust module not available")


def generate_test_audio_chunks(num_chunks: int = 1000, chunk_size: int = 1280) -> List[str]:
    """Generate test audio chunks (base64-encoded)"""
    print(f"Generating {num_chunks} test chunks of {chunk_size} bytes each...")
    chunks = []
    for i in range(num_chunks):
        # Create fake audio data
        audio_data = bytes([i % 256] * chunk_size)
        base64_data = base64.b64encode(audio_data).decode('utf-8')
        chunks.append(base64_data)
    print(f"✅ Generated {num_chunks} chunks ({len(chunks[0])} chars each)")
    return chunks


def benchmark_python_processing(chunks: List[str], iterations: int = 3) -> Dict:
    """Benchmark pure Python audio processing"""
    print(f"\n🐍 Benchmarking Python processing ({iterations} iterations)...")

    times = []
    decode_times = []
    buffer_times = []

    for iteration in range(iterations):
        buffer = bytearray()
        chunk_count = 0

        total_decode_time = 0
        total_buffer_time = 0

        start = time.perf_counter()

        for chunk in chunks:
            # Decode base64
            decode_start = time.perf_counter()
            audio_data = base64.b64decode(chunk)
            decode_time = time.perf_counter() - decode_start
            total_decode_time += decode_time

            # Buffer append
            buffer_start = time.perf_counter()
            buffer.extend(audio_data)
            buffer_time = time.perf_counter() - buffer_start
            total_buffer_time += buffer_time

            chunk_count += 1

        elapsed = time.perf_counter() - start
        times.append(elapsed)
        decode_times.append(total_decode_time)
        buffer_times.append(total_buffer_time)

        print(f"  Iteration {iteration + 1}: {elapsed:.4f}s ({chunk_count} chunks, {len(buffer)} bytes)")

    return {
        'mode': 'Python',
        'num_chunks': len(chunks),
        'total_bytes': len(buffer),
        'iterations': iterations,
        'times': times,
        'avg_time': statistics.mean(times),
        'min_time': min(times),
        'max_time': max(times),
        'stdev': statistics.stdev(times) if len(times) > 1 else 0,
        'avg_decode_time': statistics.mean(decode_times),
        'avg_buffer_time': statistics.mean(buffer_times),
        'throughput': len(chunks) / statistics.mean(times)
    }


def benchmark_rust_processing(chunks: List[str], iterations: int = 3) -> Dict:
    """Benchmark Rust audio processing"""
    if not RUST_AVAILABLE:
        return None

    print(f"\n🦀 Benchmarking Rust processing ({iterations} iterations)...")

    times = []
    decode_times = []

    for iteration in range(iterations):
        processor = callbot_rust.AudioProcessor()
        call_id = f"bench_test_{iteration}"

        total_decode_time = 0

        start = time.perf_counter()

        for chunk in chunks:
            # Rust: decode + buffer in one operation
            decode_start = time.perf_counter()
            processor.process_chunk(call_id, chunk)
            decode_time = time.perf_counter() - decode_start
            total_decode_time += decode_time

        elapsed = time.perf_counter() - start
        times.append(elapsed)
        decode_times.append(total_decode_time)

        # Get stats
        length, chunk_count, capacity = processor.get_stats(call_id)

        print(f"  Iteration {iteration + 1}: {elapsed:.4f}s ({chunk_count} chunks, {length} bytes)")

    return {
        'mode': 'Rust',
        'num_chunks': len(chunks),
        'total_bytes': length,
        'iterations': iterations,
        'times': times,
        'avg_time': statistics.mean(times),
        'min_time': min(times),
        'max_time': max(times),
        'stdev': statistics.stdev(times) if len(times) > 1 else 0,
        'avg_decode_time': statistics.mean(decode_times),
        'throughput': len(chunks) / statistics.mean(times)
    }


def benchmark_base64_decode_only(chunks: List[str], iterations: int = 3) -> Dict:
    """Benchmark only base64 decoding (Rust vs Python)"""
    print(f"\n📊 Benchmarking Base64 Decode Only...")

    results = {
        'python': [],
        'rust': []
    }

    # Python base64
    print("  🐍 Python base64...")
    for iteration in range(iterations):
        start = time.perf_counter()
        for chunk in chunks:
            _ = base64.b64decode(chunk)
        elapsed = time.perf_counter() - start
        results['python'].append(elapsed)
        print(f"    Iteration {iteration + 1}: {elapsed:.4f}s")

    # Rust base64
    if RUST_AVAILABLE:
        print("  🦀 Rust base64...")
        for iteration in range(iterations):
            start = time.perf_counter()
            for chunk in chunks:
                _ = callbot_rust.decode_base64(chunk)
            elapsed = time.perf_counter() - start
            results['rust'].append(elapsed)
            print(f"    Iteration {iteration + 1}: {elapsed:.4f}s")

    return {
        'python_avg': statistics.mean(results['python']),
        'rust_avg': statistics.mean(results['rust']) if results['rust'] else None,
        'speedup': statistics.mean(results['python']) / statistics.mean(results['rust']) if results['rust'] else None
    }


def print_comparison(python_results: Dict, rust_results: Dict):
    """Print detailed comparison"""
    print("\n" + "=" * 80)
    print("📊 BENCHMARK RESULTS COMPARISON")
    print("=" * 80)

    print(f"\nTest Configuration:")
    print(f"  - Number of chunks: {python_results['num_chunks']}")
    print(f"  - Total data size: {python_results['total_bytes'] / 1024 / 1024:.2f} MB")
    print(f"  - Iterations: {python_results['iterations']}")

    print(f"\n🐍 Python Results:")
    print(f"  - Average time: {python_results['avg_time']:.4f}s")
    print(f"  - Min time: {python_results['min_time']:.4f}s")
    print(f"  - Max time: {python_results['max_time']:.4f}s")
    print(f"  - Stdev: {python_results['stdev']:.4f}s")
    print(f"  - Throughput: {python_results['throughput']:.2f} chunks/s")
    print(f"  - Avg decode time: {python_results['avg_decode_time']*1000:.2f}ms")
    print(f"  - Avg buffer time: {python_results['avg_buffer_time']*1000:.2f}ms")

    if rust_results:
        print(f"\n🦀 Rust Results:")
        print(f"  - Average time: {rust_results['avg_time']:.4f}s")
        print(f"  - Min time: {rust_results['min_time']:.4f}s")
        print(f"  - Max time: {rust_results['max_time']:.4f}s")
        print(f"  - Stdev: {rust_results['stdev']:.4f}s")
        print(f"  - Throughput: {rust_results['throughput']:.2f} chunks/s")
        print(f"  - Avg decode time: {rust_results['avg_decode_time']*1000:.2f}ms")

        speedup = python_results['avg_time'] / rust_results['avg_time']
        throughput_increase = ((rust_results['throughput'] - python_results['throughput']) /
                               python_results['throughput'] * 100)

        print(f"\n⚡ Performance Improvement:")
        print(f"  - Speedup: {speedup:.2f}x faster")
        print(f"  - Time saved: {(python_results['avg_time'] - rust_results['avg_time'])*1000:.2f}ms")
        print(f"  - Throughput increase: {throughput_increase:.1f}%")

        decode_speedup = python_results['avg_decode_time'] / rust_results['avg_decode_time']
        print(f"  - Base64 decode speedup: {decode_speedup:.2f}x")

        # Memory efficiency
        print(f"\n💾 Memory Efficiency:")
        print(f"  - Python buffer reallocations: ~{python_results['num_chunks'] // 10} (estimated)")
        print(f"  - Rust pre-allocated capacity: 10 MB (zero reallocations)")

    print("\n" + "=" * 80)


def main():
    """Main benchmark function"""
    print("=" * 80)
    print("🎯 Rust vs Python Audio Processing Benchmark")
    print("=" * 80)

    if not RUST_AVAILABLE:
        print("❌ Rust module not available. Please build it with:")
        print("   cd callbot_rust && maturin develop --release")
        return

    # Test with realistic audio stream parameters
    # 40ms chunks at 16kHz, 16-bit = 1280 bytes per chunk
    # 2564 chunks = ~102 seconds of audio (from your test file)
    NUM_CHUNKS = 2564
    CHUNK_SIZE = 1280
    ITERATIONS = 5

    # Generate test data
    chunks = generate_test_audio_chunks(NUM_CHUNKS, CHUNK_SIZE)

    # Run benchmarks
    python_results = benchmark_python_processing(chunks, ITERATIONS)
    rust_results = benchmark_rust_processing(chunks, ITERATIONS)

    # Benchmark base64 decode only
    base64_results = benchmark_base64_decode_only(chunks, ITERATIONS)

    # Print comparison
    print_comparison(python_results, rust_results)

    # Base64 decode comparison
    if base64_results['rust_avg']:
        print(f"\n📈 Base64 Decode Only:")
        print(f"  - Python: {base64_results['python_avg']:.4f}s")
        print(f"  - Rust: {base64_results['rust_avg']:.4f}s")
        print(f"  - Speedup: {base64_results['speedup']:.2f}x")

    # Save results to JSON
    results_file = "benchmark_results.json"
    with open(results_file, 'w') as f:
        json.dump({
            'python': python_results,
            'rust': rust_results,
            'base64': base64_results,
            'config': {
                'num_chunks': NUM_CHUNKS,
                'chunk_size': CHUNK_SIZE,
                'iterations': ITERATIONS
            }
        }, f, indent=2)
    print(f"\n📁 Results saved to: {results_file}")


if __name__ == "__main__":
    main()
