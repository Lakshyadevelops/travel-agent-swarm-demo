# Valkey vs Postgres across store sizes and concurrent users

Host 64 cores · commit d7d714d+dirty · end-to-end time per planning session (what a user waits for) · LLM latency replayed from the committed trace, zero API calls.

- **valkey 2 vCPU**: 2 GB RAM; appendonly=no, io-threads=2, maxmemory=1610612736, maxmemory-policy=allkeys-lru, save=3600 1 300 100 60 10000
- **postgres 2 vCPU**: 2 GB RAM; max_connections=100, shared_buffers=512MB, effective_cache_size=1536MB, work_mem=2621kB, synchronous_commit=on, max_worker_processes=2, max_wal_size=4GB
- **valkey 4 vCPU**: 2 GB RAM; appendonly=no, io-threads=4, maxmemory=1610612736, maxmemory-policy=allkeys-lru, save=3600 1 300 100 60 10000
- **postgres 4 vCPU**: 2 GB RAM; max_connections=100, shared_buffers=512MB, effective_cache_size=1536MB, work_mem=2621kB, synchronous_commit=on, max_worker_processes=4, max_wal_size=4GB

## 1 scratchpad write per agent step

Median session time, seconds. Gap = paired median difference on the same trips, bootstrap 95% CI.

| Users | Valkey 2 vCPU · p50 | Postgres 2 vCPU · p50 | Gap 2 vCPU (Valkey − Postgres) | Valkey 4 vCPU · p50 | Postgres 4 vCPU · p50 | Gap 4 vCPU (Valkey − Postgres) |
|---|---|---|---|---|---|---|
| 1 | 36.88 | 36.92 | -40 ms [-52, -21] | 36.89 | 36.92 | -35 ms [-100, -24] |
| 10 | 36.97 | 37.00 | -28 ms (not significant) | 36.98 | 37.00 | -26 ms (not significant) |
| 100 | 37.48 | 37.51 | -23 ms [-43, -18] | 37.48 | 37.52 | -39 ms [-87, -27] |
| 300 | 37.35 | 37.37 | -21 ms (not significant) | 37.34 | 37.36 | -28 ms [-53, -8] |
| 1,000 | 37.23 | 37.28 | -42 ms [-54, -23] | 37.23 | 37.28 | -43 ms [-59, -26] |
| 2,000 | 37.35 | 37.41 | -65 ms [-83, -44] | 37.35 | 37.39 | -35 ms [-53, -28] |
| 3,000 | 37.36 | 38.17 | -808 ms [-856, -762] | 37.36 | 37.41 | -50 ms [-55, -33] |

| Store vCPU | Users | Store | p50 / p95 / p99 (s) | Sessions/min | Failed | Store CPU avg / max | Store mem max | Client-bound? |
|---|---|---|---|---|---|---|---|---|
| 2 | 1 | valkey | 36.882 / 55.466 / 55.466 | 1.4 | 0 | 1.4% / 4.1% | 21.6 MB | no |
| 2 | 1 | postgres | 36.922 / 55.534 / 55.534 | 1.4 | 0 | 1.4% / 5.5% | 62.9 MB | no |
| 2 | 10 | valkey | 36.973 / 52.187 / 55.988 | 15.1 | 0 | 1.5% / 4.6% | 22.8 MB | no |
| 2 | 10 | postgres | 37.001 / 52.249 / 56.04 | 15.1 | 0 | 2.1% / 9.8% | 102.0 MB | no |
| 2 | 100 | valkey | 37.485 / 51.94 / 62.86 | 155.3 | 0 | 2.7% / 5.4% | 24.9 MB | no |
| 2 | 100 | postgres | 37.514 / 51.996 / 62.908 | 155.3 | 0 | 6.4% / 13.9% | 178.8 MB | no |
| 2 | 300 | valkey | 37.351 / 53.426 / 63.032 | 461.7 | 0 | 4.6% / 10.7% | 29.8 MB | no |
| 2 | 300 | postgres | 37.37 / 53.466 / 63.098 | 461.3 | 0 | 16.4% / 21.9% | 218.1 MB | no |
| 2 | 1,000 | valkey | 37.234 / 53.978 / 62.114 | 1,537.3 | 0 | 10.1% / 24.6% | 48.3 MB | no |
| 2 | 1,000 | postgres | 37.282 / 54.036 / 62.179 | 1,536.3 | 0 | 54.0% / 68.5% | 459.9 MB | no |
| 2 | 2,000 | valkey | 37.347 / 53.853 / 62.179 | 3,058.0 | 0 | 16.8% / 21.2% | 67.3 MB | no |
| 2 | 2,000 | postgres | 37.413 / 53.914 / 62.313 | 3,050.0 | 0 | 109.6% / 158.6% | 633.3 MB | no |
| 2 | 3,000 | valkey | 37.362 / 53.62 / 62.05 | 4,579.3 | 0 | 24.1% / 30.2% | 89.0 MB | no |
| 2 | 3,000 | postgres | 38.17 / 54.898 / 63.719 | 4,458.0 | 0 | 173.5% / 211.9% | 807.6 MB | no |
| 4 | 1 | valkey | 36.887 / 55.451 / 55.451 | 1.4 | 0 | 1.4% / 4.5% | 20.5 MB | no |
| 4 | 1 | postgres | 36.922 / 55.525 / 55.525 | 1.4 | 0 | 1.4% / 5.4% | 63.2 MB | no |
| 4 | 10 | valkey | 36.975 / 52.193 / 55.985 | 15.1 | 0 | 1.5% / 4.6% | 21.2 MB | no |
| 4 | 10 | postgres | 37.001 / 52.244 / 56.028 | 15.1 | 0 | 2.3% / 18.6% | 106.3 MB | no |
| 4 | 100 | valkey | 37.479 / 51.944 / 62.848 | 155.3 | 0 | 2.6% / 5.3% | 23.2 MB | no |
| 4 | 100 | postgres | 37.522 / 51.983 / 62.936 | 155.3 | 0 | 7.9% / 49.7% | 188.8 MB | no |
| 4 | 300 | valkey | 37.34 / 53.428 / 63.045 | 462.0 | 0 | 4.5% / 8.4% | 28.2 MB | no |
| 4 | 300 | postgres | 37.359 / 53.448 / 63.108 | 461.3 | 0 | 16.9% / 33.4% | 248.3 MB | no |
| 4 | 1,000 | valkey | 37.233 / 53.977 / 62.082 | 1,537.0 | 0 | 10.2% / 25.5% | 46.3 MB | no |
| 4 | 1,000 | postgres | 37.282 / 54.039 / 62.161 | 1,536.3 | 0 | 54.8% / 85.0% | 505.4 MB | no |
| 4 | 2,000 | valkey | 37.351 / 53.817 / 62.234 | 3,057.7 | 0 | 16.9% / 20.4% | 68.2 MB | no |
| 4 | 2,000 | postgres | 37.388 / 53.914 / 62.244 | 3,053.3 | 0 | 111.4% / 153.4% | 625.8 MB | no |
| 4 | 3,000 | valkey | 37.359 / 53.641 / 62.055 | 4,579.0 | 0 | 24.7% / 58.6% | 98.5 MB | no |
| 4 | 3,000 | postgres | 37.407 / 53.692 / 62.144 | 4,572.3 | 0 | 162.2% / 195.9% | 801.4 MB | no |

## 10 scratchpad writes per agent step

Median session time, seconds. Gap = paired median difference on the same trips, bootstrap 95% CI.

| Users | Valkey 2 vCPU · p50 | Postgres 2 vCPU · p50 | Gap 2 vCPU (Valkey − Postgres) | Valkey 4 vCPU · p50 | Postgres 4 vCPU · p50 | Gap 4 vCPU (Valkey − Postgres) |
|---|---|---|---|---|---|---|
| 1 | 36.91 | 36.99 | -75 ms [-148, -65] | 36.91 | 36.98 | -78 ms [-241, -58] |
| 10 | 36.99 | 37.07 | -79 ms [-99, -20] | 36.98 | 37.06 | -76 ms (not significant) |
| 100 | 37.51 | 37.58 | -71 ms [-118, -38] | 37.50 | 37.58 | -77 ms [-116, -38] |
| 300 | 37.36 | 37.46 | -100 ms [-135, -76] | 37.36 | 37.47 | -105 ms [-139, -72] |
| 1,000 | 37.27 | 37.52 | -258 ms [-290, -206] | 37.26 | 37.39 | -136 ms [-154, -112] |
| 2,000 | 37.38 | 66.91 | -29.0 s [-29.2, -28.8] | 37.39 | 37.57 | -177 ms [-207, -162] |
| 3,000 | 37.42 | 99.89 | -54.0 s [-54.7, -53.5] | 37.41 | 51.69 | -14.3 s [-14.4, -14.2] |

| Store vCPU | Users | Store | p50 / p95 / p99 (s) | Sessions/min | Failed | Store CPU avg / max | Store mem max | Client-bound? |
|---|---|---|---|---|---|---|---|---|
| 2 | 1 | valkey | 36.911 / 55.504 / 55.504 | 1.4 | 0 | 1.4% / 4.5% | 22.5 MB | no |
| 2 | 1 | postgres | 36.986 / 55.713 / 55.713 | 1.4 | 0 | 1.5% / 6.0% | 67.0 MB | no |
| 2 | 10 | valkey | 36.988 / 52.221 / 56.012 | 15.1 | 0 | 1.6% / 4.5% | 25.8 MB | no |
| 2 | 10 | postgres | 37.067 / 52.364 / 56.157 | 15.1 | 0 | 3.2% / 21.3% | 135.9 MB | no |
| 2 | 100 | valkey | 37.512 / 51.964 / 62.922 | 155.3 | 0 | 3.1% / 10.3% | 32.7 MB | no |
| 2 | 100 | postgres | 37.582 / 52.119 / 63.104 | 155.0 | 0 | 15.1% / 53.4% | 227.0 MB | no |
| 2 | 300 | valkey | 37.363 / 53.466 / 63.103 | 461.3 | 0 | 6.7% / 20.9% | 51.9 MB | no |
| 2 | 300 | postgres | 37.464 / 53.593 / 63.319 | 460.7 | 0 | 43.5% / 89.2% | 381.1 MB | no |
| 2 | 1,000 | valkey | 37.267 / 54.031 / 62.172 | 1,536.3 | 0 | 19.1% / 57.4% | 117.8 MB | no |
| 2 | 1,000 | postgres | 37.52 / 54.358 / 62.497 | 1,523.7 | 0 | 155.8% / 207.2% | 859.0 MB | no |
| 2 | 2,000 | valkey | 37.384 / 53.87 / 62.32 | 3,053.0 | 0 | 88.8% / 157.0% | 194.8 MB | no |
| 2 | 2,000 | postgres | 66.905 / 103.408 / 134.089 | 1,621.7 | 0 | 202.2% / 207.7% | 918.7 MB | no |
| 2 | 3,000 | valkey | 37.419 / 53.772 / 62.198 | 4,569.7 | 0 | 147.4% / 198.9% | 294.9 MB | no |
| 2 | 3,000 | postgres | 99.894 / 160.203 / 200.187 | 1,594.0 | 0 | 201.2% / 209.4% | 933.2 MB | no |
| 4 | 1 | valkey | 36.905 / 55.494 / 55.494 | 1.4 | 0 | 1.5% / 4.7% | 21.2 MB | no |
| 4 | 1 | postgres | 36.983 / 55.695 / 55.695 | 1.4 | 0 | 1.8% / 5.6% | 65.4 MB | no |
| 4 | 10 | valkey | 36.982 / 52.227 / 56.026 | 15.1 | 0 | 1.8% / 4.4% | 23.2 MB | no |
| 4 | 10 | postgres | 37.058 / 52.347 / 56.156 | 15.1 | 0 | 3.4% / 45.0% | 129.2 MB | no |
| 4 | 100 | valkey | 37.501 / 51.973 / 63.021 | 155.3 | 0 | 3.4% / 7.3% | 30.9 MB | no |
| 4 | 100 | postgres | 37.579 / 52.099 / 63.095 | 155.0 | 0 | 16.1% / 54.8% | 257.2 MB | no |
| 4 | 300 | valkey | 37.362 / 53.453 / 63.09 | 461.7 | 0 | 7.2% / 11.1% | 50.0 MB | no |
| 4 | 300 | postgres | 37.467 / 53.61 / 63.315 | 460.7 | 0 | 44.0% / 84.6% | 417.4 MB | no |
| 4 | 1,000 | valkey | 37.259 / 54.028 / 62.192 | 1,536.0 | 0 | 18.8% / 25.2% | 111.6 MB | no |
| 4 | 1,000 | postgres | 37.393 / 54.195 / 62.5 | 1,529.3 | 0 | 153.0% / 219.2% | 867.6 MB | no |
| 4 | 2,000 | valkey | 37.39 / 53.934 / 62.284 | 3,054.3 | 0 | 99.7% / 139.0% | 194.5 MB | no |
| 4 | 2,000 | postgres | 37.566 / 54.221 / 62.812 | 3,034.7 | 0 | 316.8% / 389.6% | 911.6 MB | no |
| 4 | 3,000 | valkey | 37.413 / 53.722 / 62.184 | 4,569.0 | 0 | 145.7% / 239.1% | 276.4 MB | no |
| 4 | 3,000 | postgres | 51.694 / 77.597 / 96.207 | 3,175.3 | 0 | 400.7% / 418.3% | 952.7 MB | no |

⚠ marks a level where the load generator, not the store, set the latency.
