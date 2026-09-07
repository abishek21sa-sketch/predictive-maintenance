# Engineering Assumptions Register

| Assumption | Baseline value | Why it exists | Production replacement |
|---|---:|---|---|
| RUL training cap | 125 cycles | Standardized degradation target handling | Tune by asset/failure mode |
| Maintenance planning horizon | 30 cycles demo / 60 cycles FD001 release | Operational planning window | Site-specific planning calendar |
| Maintenance capacity | 2 units/cycle | Demonstrates resource-constrained scheduling | Crew/bay/skill calendars |
| Preventive maintenance cost | 5,000 | Relative decision-cost coefficient | ERP/CMMS actual cost |
| Failure cost | 30,000 | Penalizes unscheduled failure | Downtime + safety + quality + repair cost |
| Downtime cost | 1,200/cycle | Captures production loss | Plant economic model |
| RUL scenarios | 0.8x / 1.0x / 1.2x | Represents forecast uncertainty | Empirical predictive intervals |
| Scenario probabilities | 0.2 / 0.6 / 0.2 | Simple stochastic baseline | Calibrated residual distribution |
| FMEA ratings | Illustrative | Enables FMEA→RCM workflow | Cross-functional plant FMEA |

## Real Operations Command planning assumptions

MetroPT-3 supplies real compressor telemetry and published failure timing, but it does not publish a complete maintenance-shop economic/resource contract. The `/operations` decision layer therefore makes these assumptions explicit rather than implying they were observed:

- preventive service cost: modeled scenario input;
- corrective/failure consequence: modeled scenario input;
- downtime cost per planning cycle: modeled scenario input;
- 1 planning cycle = 2 hours;
- maintenance bays, mechanic/electrical skill capacity and parts are scenario inputs;
- SHOP-7101/7102/7103 are assumed backlog jobs used for resource-contention analysis;
- a 24-hour failure probability is transformed into a risk-equivalent scheduling deadline and is **not called RUL**.

These assumptions are surfaced in API/UI evidence and can be changed without modifying observed MetroPT telemetry.
