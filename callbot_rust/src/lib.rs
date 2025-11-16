use pyo3::prelude::*;
use pyo3::exceptions::PyValueError;
use bytes::BytesMut;
use base64::{Engine as _, engine::general_purpose};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};

/// High-performance audio buffer using Rust's BytesMut for efficient memory management
#[pyclass]
struct AudioBuffer {
    buffer: BytesMut,
    chunk_count: usize,
    total_bytes: usize,
}

#[pymethods]
impl AudioBuffer {
    #[new]
    fn new() -> Self {
        AudioBuffer {
            // Pre-allocate 10MB buffer to avoid reallocations
            buffer: BytesMut::with_capacity(10_000_000),
            chunk_count: 0,
            total_bytes: 0,
        }
    }

    /// Add base64-encoded audio chunk - optimized single operation
    fn add_base64_chunk(&mut self, payload: &str) -> PyResult<usize> {
        let decoded = general_purpose::STANDARD
            .decode(payload)
            .map_err(|e| PyValueError::new_err(format!("Base64 decode error: {}", e)))?;

        let len = decoded.len();
        self.buffer.extend_from_slice(&decoded);
        self.chunk_count += 1;
        self.total_bytes += len;

        Ok(len)
    }

    /// Add raw bytes directly (for binary WebSocket frames)
    fn add_bytes(&mut self, data: &[u8]) -> PyResult<usize> {
        let len = data.len();
        self.buffer.extend_from_slice(data);
        self.chunk_count += 1;
        self.total_bytes += len;
        Ok(len)
    }

    /// Get buffer as bytes (zero-copy when possible)
    fn to_bytes(&self) -> Vec<u8> {
        self.buffer.to_vec()
    }

    /// Get buffer length
    fn len(&self) -> usize {
        self.buffer.len()
    }

    /// Get chunk count
    fn chunk_count(&self) -> usize {
        self.chunk_count
    }

    /// Clear buffer for reuse
    fn clear(&mut self) {
        self.buffer.clear();
        self.chunk_count = 0;
        self.total_bytes = 0;
    }

    /// Get capacity (for monitoring)
    fn capacity(&self) -> usize {
        self.buffer.capacity()
    }
}

/// Audio processor that manages multiple call buffers efficiently
#[pyclass]
struct AudioProcessor {
    buffers: Arc<Mutex<HashMap<String, AudioBuffer>>>,
}

#[pymethods]
impl AudioProcessor {
    #[new]
    fn new() -> Self {
        AudioProcessor {
            buffers: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    /// Process a base64-encoded chunk for a specific call_id
    fn process_chunk(&self, call_id: &str, base64_payload: &str) -> PyResult<usize> {
        let mut buffers = self.buffers.lock().unwrap();
        let buffer = buffers.entry(call_id.to_string())
            .or_insert_with(AudioBuffer::new);
        buffer.add_base64_chunk(base64_payload)
    }

    /// Process raw bytes for a specific call_id
    fn process_bytes(&self, call_id: &str, data: &[u8]) -> PyResult<usize> {
        let mut buffers = self.buffers.lock().unwrap();
        let buffer = buffers.entry(call_id.to_string())
            .or_insert_with(AudioBuffer::new);
        buffer.add_bytes(data)
    }

    /// Get buffer statistics
    fn get_stats(&self, call_id: &str) -> PyResult<(usize, usize, usize)> {
        let buffers = self.buffers.lock().unwrap();
        if let Some(buffer) = buffers.get(call_id) {
            Ok((buffer.len(), buffer.chunk_count(), buffer.capacity()))
        } else {
            Ok((0, 0, 0))
        }
    }

    /// Get buffer as bytes
    fn get_buffer(&self, call_id: &str) -> PyResult<Vec<u8>> {
        let buffers = self.buffers.lock().unwrap();
        if let Some(buffer) = buffers.get(call_id) {
            Ok(buffer.to_bytes())
        } else {
            Ok(Vec::new())
        }
    }

    /// Clear buffer for a specific call
    fn clear_buffer(&self, call_id: &str) -> PyResult<()> {
        let mut buffers = self.buffers.lock().unwrap();
        buffers.remove(call_id);
        Ok(())
    }

    /// Get number of active buffers
    fn active_buffers(&self) -> usize {
        let buffers = self.buffers.lock().unwrap();
        buffers.len()
    }
}

/// Fast JSON message parser for WebSocket messages
#[pyfunction]
fn parse_media_message(json_str: &str) -> PyResult<Option<String>> {
    let value: serde_json::Value = serde_json::from_str(json_str)
        .map_err(|e| PyValueError::new_err(format!("JSON parse error: {}", e)))?;

    if let Some(payload) = value.get("media")
        .and_then(|m| m.get("payload"))
        .and_then(|p| p.as_str()) {
        Ok(Some(payload.to_string()))
    } else {
        Ok(None)
    }
}

/// Decode base64 string to bytes (standalone function for benchmarking)
#[pyfunction]
fn decode_base64(payload: &str) -> PyResult<Vec<u8>> {
    general_purpose::STANDARD
        .decode(payload)
        .map_err(|e| PyValueError::new_err(format!("Base64 decode error: {}", e)))
}

/// Python module definition
#[pymodule]
fn callbot_rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<AudioBuffer>()?;
    m.add_class::<AudioProcessor>()?;
    m.add_function(wrap_pyfunction!(parse_media_message, m)?)?;
    m.add_function(wrap_pyfunction!(decode_base64, m)?)?;
    Ok(())
}
