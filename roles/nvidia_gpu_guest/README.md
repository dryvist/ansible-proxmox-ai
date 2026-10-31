# nvidia_gpu_guest

Installs shared NVIDIA userspace, the CUDA toolkit, uv, Hugging Face tooling, and
the writable model cache.

Opted-in GPU serving guests set every NVML fan to 100% during converge and at
boot. Unsupported fan control is reported by NVML without a fallback override.
Set `nvidia_gpu_guest_power_limit_w` to a supported wattage to apply it during
converge and at boot; the default `0` leaves the driver default. The unit name
is set by `nvidia_gpu_guest_power_limit_unit_name` (default
`nvidia-gpu-power-limit.service`).
The role does not install or select a serving engine.

Engine roles call its `cache-sync.yml` and `load-registry.yml` task files with their own profile set.
