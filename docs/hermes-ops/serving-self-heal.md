# Serving self-heal (the zombie watchdog)

The Mac serving host runs llama-swap under a launchd agent whose `KeepAlive`
only restarts on process **exit**. llama-swap can panic (`sync: WaitGroup is
reused`) into a process that stays alive and holds the listen socket but
answers nothing — a zombie launchd never notices — so every request gets
connection-refused and litellm surfaces `MidStreamFallbackError` until a human
intervenes.

The serving layer (in the nix-ai MLX module) now ships a **liveness watchdog**:
a launchd agent probes the proxy's own `/v1/models` every 60s and, on two
consecutive failures, `launchctl kickstart`s the server agent. It gates
re-fires with a cooldown marker so a 20–60s model reload is not
restart-stormed. Health, not PID. This is the durable fix for the recurring
`MidStreamFallbackError` outage; a manual `launchctl kickstart` remains the
sanctioned break-fix if the watchdog is not yet deployed.
