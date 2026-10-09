# nvidia_gpu_guest

Installs shared NVIDIA userspace, the CUDA toolkit, uv, Hugging Face tooling, and the writable model cache. It does not install or select a serving engine.

Engine roles call its `cache-sync.yml` and `load-registry.yml` task files with their own profile set.
