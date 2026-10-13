# Safety rules

This platform can issue commands to live power equipment at a factory. These rules
are what keep it from causing an outage or damaging equipment. Most of them are
enforced in code; the rest are operating procedure.

## The invariant
The plant runs normally with this system switched off. It is never in the
critical control path. InteliNeo + PLC logic retain authority at all times.

## Enforced in code
- **No raw writes.** Only `CommandDefinition`s from the registry can be executed.
  Adapters refuse anything that isn't a registered command's `raw` mapping.
- **C4 is unloadable.** Protections, e-stop, breakers (GCB/MCB), synchronization
  cannot be expressed as commands; the model rejects them at load time.
- **One path.** HMI, AI and autopilot all submit through `CommandGateway`. No bypass.
- **AI always needs a human.** Role `ai` → `requires_approval` forced true.
  Automation may only run C2 setpoints, only in `autonomous` mode, only on the allowlist.
- **Nobody approves their own command.** Except engineers, for commissioning.
- **Recheck before execute.** Preconditions are re-evaluated after approval.
- **Verify by readback.** A write is `verified` only when the device confirms it.
  Timeout → `ambiguous`: no retry, no auto-revert, human reconciles.
- **Fresh data or nothing.** Stale/bad telemetry blocks writes on that device.
- **Physical preconditions.** Min run / min stop time, firm reserve before stopping a
  genset, SOC window, BMS live limits, min stable load, curtail floor, controller not OFF.
- **Rate limits** per device+command and an hourly budget for automation.
- **Autopilot trips** to `restricted` behaviour after 3 ticks with failed/ambiguous
  writes. Re-enabling is a manual, audited action.
- **Hash-chained audit log.** Every submission, decision, approval, execution and
  readback. `verify()` detects edits.

## Operating procedure (not code)
1. Go live **read-only** first. Commission every point: value, unit, scale, sign, timestamp.
2. Then `supervised` with ONE harmless C2 setpoint, human-approved, for weeks.
3. `autonomous` only for the allowlist in `site.yaml`, after shadow-mode comparison
   (planner output vs. what operators actually did) shows it is sane.
4. Genset start/stop stay human-approved. If the site later wants them automated,
   that is a separate risk assessment with the installer and the plant owner.
5. Replace the header-based auth stub with a real identity provider before the API
   is reachable from anywhere but localhost.
6. The ComAp controller password / access level stays with the site. The platform
   gets the narrowest Modbus or WebSupervisor permission that works.

## What the AI is for
Explain, detect, predict, plan, propose, report. It does not execute, approve,
change policy, or touch an adapter. If the model is wrong, the policy engine and
the controller's own protections are what stand between the error and the plant.
