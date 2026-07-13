# P7.4 Corrective Raw-Audit Evidence

Expected summaries were derived from frozen raw bytes with a standalone manual format audit, not by invoking P7.4 parser, normalizer, replay, comparator, or actual-output code. The BTF comma-record audit found: `example.btf` 2389 records, first/last `148958/166226`; `example-4cores.btf` 25228, `213463/707750`; `example-50k.btf` 50001, `405/402861`. The VCD scalar audit counted 15084 `0/1/x/X/z/Z` scalar records, first/last `213463/693083`.

The same raw audit found the 50k first decreasing source-order pair at source record 2037, physical line 2042, `26113 -> 26112`. It found the VCD `x` scalar at physical line 15440. These facts establish the expected-fixture authoring evidence; they do not bless production output. The VCD parser contract rejects the `x` scalar before comparator invocation, so that case is formal `replay_fail` with `comparison_attempted=false`; no replay-pass condition or tolerance is weakened.
