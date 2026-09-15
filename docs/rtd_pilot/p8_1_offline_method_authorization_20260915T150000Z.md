# P8.1 Offline Method Authorization

The project owner authorized a new versioned offline method-output stage over
the 63 already frozen P5 UART raw captures. This amendment adds no capture,
hardware operation, P7 rewrite, parameter tuning, or truth generation.

The method is fixed before execution: strip boot metadata, decode only valid
ten-byte `0xA5` UART frames, and predict manifestation from these fixed event
sets: F1 `{13,14}`, F2 `{20,21,22,23}`, and F3 `{30}`. It neither reads case
identifiers/splits from raw paths nor reads Observer/OAR/CVR. The scorer alone
joins opaque capture IDs to P5's admitted manifestation field and publishes
aggregate case-clustered results only.
