// Serial stand-ins for the three libgomp entry points DeepStruggle's
// bindings/gomp_parallel.hpp calls, for the native shared-library build of the
// shim (scripts/build_deep_wasm.sh with DEEP_CXX). The WebAssembly build never
// reaches them (rollout_batch.cpp's loops are serial under __EMSCRIPTEN__), and
// this keeps the native build free of an OpenMP runtime on every platform.
extern "C" {
void GOMP_parallel(void (*fn)(void*), void* data, unsigned, unsigned) { fn(data); }
int omp_get_thread_num(void) { return 0; }
int omp_get_num_threads(void) { return 1; }
}
