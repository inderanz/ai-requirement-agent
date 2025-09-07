## Task Intent: AU NPP Business & Technical Requirements
Produce:
1) Functional Requirements (FR)
   - Payee addressing (PayID, BSB/Account), real‑time clearing/settlement paths, Osko flows.
   - Consent/mandates lifecycle (PayTo): create, verify, amend, suspend, revoke; dispute handling.
   - Confirmation of Payee (name matching & outcomes); exception flows and reversals.
2) Non‑Functional Requirements (NFR)
   - Availability (e.g., ≥99.95%), end‑to‑end latency (seconds), throughput.
   - Resilience: multi‑AZ/region, RPO/RTO, chaos/failover, back‑pressure & rate limiting.
   - Observability: metrics, traces, structured logs, audit trails, reconciliation.
3) Interfaces & Message Model
   - ISO 20022 messages used: required vs conditional fields, idempotency, correlation IDs.
   - Error semantics and retry windows.
4) Controls & Compliance (AU)
   - APRA CPS 234 (IS) & CPS 230 (operational risk/resilience).
   - OAIC APPs (privacy), AU AI Principles / Voluntary AI Safety (if AI present).
5) Data & Retention
   - Settlement data, consent artifacts, logs, PII/PayID handling & masking.
6) Acceptance Criteria
   - Scenario‑driven tests per FR/NFR with realistic NPP flows and failure cases.