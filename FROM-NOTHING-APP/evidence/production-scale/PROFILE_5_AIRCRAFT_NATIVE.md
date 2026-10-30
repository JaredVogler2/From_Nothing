# Profile: 5 production-shaped aircraft, native core, FORECAST, no search moves

cProfile of `schedule_snapshot` on the same 5-aircraft production-shaped snapshot as `benchmark_production.json`
(27,000 jobs, 44,148 phases, 126,848 dependencies, 710 people). The profiler roughly doubles wall time, so read the
shares, not the seconds: unprofiled, this run takes 239 s.

`profiled wall seconds: 409.8 {'requested': True, 'available': True, 'engine': 'native'} [('neutral_seed', 197500), ('economic_redispatch', 138006), ('delay_repair', 8), ('neighborhoods', 0)]`

## By cumulative time

```
   List reduced from 732 to 60 due to restriction <60>
   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
        1    0.163    0.163  409.623  409.623 focus/engine/max/optimizer/orchestrator.py:437(schedule_snapshot)
        1    0.044    0.044  335.515  335.515 focus/engine/max/optimizer/orchestrator.py:215(run)
        2    0.008    0.004  267.654  133.827 focus/engine/max/optimizer/orchestrator.py:153(decode)
        2    0.644    0.322  267.646  133.823 focus/engine/max/optimizer/greedy.py:169(run)
    66696    0.280    0.000  259.634    0.004 focus/engine/max/optimizer/greedy.py:248(_place)
    34296    8.237    0.000  256.281    0.007 focus/engine/max/optimizer/greedy.py:277(_search)
  2377953   12.094    0.000  211.281    0.000 focus/engine/max/optimizer/greedy.py:313(_book)
  2405989    4.398    0.000  126.441    0.000 focus/codec.py:165(canonical_dumps)
  2377953    5.067    0.000  108.144    0.000 focus/engine/crew_selection.py:55(choose_crew)
30922634/2434017   43.658    0.000  100.900    0.000 focus/codec.py:121(to_jsonable)
  2377953    6.944    0.000  100.556    0.000 focus/engine/native.py:190(choose)
  2377953   85.602    0.000   85.602    0.000 {method 'choose_crew' of 'focus_native.Ledger' objects}
        2    0.124    0.062   74.642   37.321 focus/validation/plan.py:667(validate)
    28028    0.040    0.000   73.423    0.003 focus/codec.py:182(content_hash)
        1    0.029    0.029   63.943   63.943 focus/engine/max/optimizer/orchestrator.py:284(_valid)
        5    0.000    0.000   63.838   12.768 focus/contracts/planning.py:35(record_hash)
  2377959    5.888    0.000   62.102    0.000 focus/domain.py:182(digest_id)
        2    0.000    0.000   48.418   24.209 focus/validation/plan.py:508(_e_lineage)
        2    0.000    0.000   31.627   15.813 focus/validation/input.py:77(snapshot_integrity)
        1    0.000    0.000   31.627   31.627 focus/contracts/planning.py:498(verify)
        1    0.000    0.000   31.627   31.627 focus/contracts/planning.py:495(computed_hash)
128638162   23.819    0.000   29.204    0.000 {built-in method builtins.isinstance}
  7924955    7.139    0.000   28.607    0.000 focus/domain.py:249(id)
  2405989    4.835    0.000   27.109    0.000 /usr/lib/python3.12/json/__init__.py:183(dumps)
        2    1.370    0.685   23.626   11.813 focus/engine/max/optimizer/orchestrator.py:317(build_candidate)
  2405989    4.078    0.000   21.167    0.000 /usr/lib/python3.12/json/encoder.py:183(encode)
        2    0.001    0.001   18.757    9.379 focus/data/repositories.py:50(put_record)
  8151755    7.908    0.000   17.554    0.000 focus/domain.py:227(id)
        2    0.000    0.000   16.785    8.393 focus/contracts/planning.py:691(computed_hash)
  2405989   15.624    0.000   15.624    0.000 /usr/lib/python3.12/json/encoder.py:205(iterencode)
        2    0.000    0.000   15.426    7.713 focus/contracts/planning.py:695(seal_candidate)
        2    0.000    0.000   13.359    6.679 focus/codec.py:410(seal)
  1814381    7.049    0.000   13.051    0.000 /usr/lib/python3.12/dataclasses.py:1271(fields)
        1    7.631    7.631   12.677   12.677 focus/engine/max/optimizer/orchestrator.py:122(__init__)
  2673881    7.729    0.000   11.553    0.000 focus/calendar.py:145(advance)
  2545636    2.330    0.000   11.488    0.000 focus/calendar.py:139(valid_start)
  2105274    6.054    0.000   10.583    0.000 {built-in method builtins.sorted}
 12944080    7.277    0.000   10.339    0.000 {method 'get' of 'dict' objects}
  1587092    3.081    0.000   10.008    0.000 focus/codec.py:50(format_utc)
  2751473    1.137    0.000    8.158    0.000 focus/contracts/planning.py:265(id)
  2187751    1.651    0.000    7.928    0.000 focus/domain.py:177(_digest)
        1    0.533    0.533    7.513    7.513 focus/engine/model.py:45(__init__)
        2    0.000    0.000    7.076    3.538 focus/codec.py:405(_envelope_hash)
  2751473    3.273    0.000    7.021    0.000 focus/domain.py:285(id)
 12680876    4.812    0.000    6.940    0.000 /usr/lib/python3.12/enum.py:202(__get__)
  2955243    1.969    0.000    6.703    0.000 focus/domain.py:44(_total_us)
  2215780    2.494    0.000    6.683    0.000 focus/codec.py:176(sha256_hex)
  2385523    1.030    0.000    6.269    0.000 focus/domain.py:57(to_minute)
  2377953    1.060    0.000    6.226    0.000 focus/engine/native.py:184(bundle_id)
  6846004    4.062    0.000    6.117    0.000 /usr/lib/python3.12/dataclasses.py:1294(is_dataclass)
```

## By own time

```
   List reduced from 732 to 30 due to restriction <30>
   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
  2377953   85.602    0.000   85.602    0.000 {method 'choose_crew' of 'focus_native.Ledger' objects}
30922634/2434017   43.658    0.000  100.900    0.000 focus/codec.py:121(to_jsonable)
128638162   23.819    0.000   29.204    0.000 {built-in method builtins.isinstance}
  2405989   15.624    0.000   15.624    0.000 /usr/lib/python3.12/json/encoder.py:205(iterencode)
  2377953   12.094    0.000  211.281    0.000 focus/engine/max/optimizer/greedy.py:313(_book)
    34296    8.237    0.000  256.281    0.007 focus/engine/max/optimizer/greedy.py:277(_search)
  8151755    7.908    0.000   17.554    0.000 focus/domain.py:227(id)
  2673881    7.729    0.000   11.553    0.000 focus/calendar.py:145(advance)
        1    7.631    7.631   12.677   12.677 focus/engine/max/optimizer/orchestrator.py:122(__init__)
 12944080    7.277    0.000   10.339    0.000 {method 'get' of 'dict' objects}
  7924955    7.139    0.000   28.607    0.000 focus/domain.py:249(id)
  1814381    7.049    0.000   13.051    0.000 /usr/lib/python3.12/dataclasses.py:1271(fields)
  2377953    6.944    0.000  100.556    0.000 focus/engine/native.py:190(choose)
  2105274    6.054    0.000   10.583    0.000 {built-in method builtins.sorted}
  2377959    5.888    0.000   62.102    0.000 focus/domain.py:182(digest_id)
  1587092    5.720    0.000    5.720    0.000 {method 'strftime' of 'datetime.date' objects}
 16409869    5.355    0.000    5.355    0.000 /usr/lib/python3.12/dataclasses.py:1286(<genexpr>)
  2377953    5.067    0.000  108.144    0.000 focus/engine/crew_selection.py:55(choose_crew)
  2405989    4.835    0.000   27.109    0.000 /usr/lib/python3.12/json/__init__.py:183(dumps)
 12680876    4.812    0.000    6.940    0.000 /usr/lib/python3.12/enum.py:202(__get__)
  2405989    4.398    0.000  126.441    0.000 focus/codec.py:165(canonical_dumps)
 19162831    4.195    0.000    4.195    0.000 {built-in method builtins.getattr}
  2405989    4.078    0.000   21.167    0.000 /usr/lib/python3.12/json/encoder.py:183(encode)
  6846004    4.062    0.000    6.117    0.000 /usr/lib/python3.12/dataclasses.py:1294(is_dataclass)
 11439325    3.870    0.000    3.994    0.000 {built-in method builtins.max}
  8151755    3.644    0.000    5.495    0.000 <string>:2(__hash__)
 13386626    3.395    0.000    3.395    0.000 {built-in method builtins.hash}
  2751473    3.273    0.000    7.021    0.000 focus/domain.py:285(id)
  1587092    3.081    0.000   10.008    0.000 focus/codec.py:50(format_utc)
  9537637    2.953    0.000    2.953    0.000 {built-in method builtins.min}
```
