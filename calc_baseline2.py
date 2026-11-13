import csv, sys, glob, os, statistics

# Hosts with sparse 300 s intervals — idle (packet_delta=0) and partial/startup noise excluded
JITTER_HOSTS = {'h4', 'h5', 'h6', 'h7', 'h9'}
JITTER_MIN_BURST = 20  # Filters out single-digit startup/restart artifacts
STARTUP_SKIP_SECONDS = 10

cut = float(sys.argv[1])
F = max(glob.glob("baseline_raw_*.csv"), key=os.path.getmtime)
data, stamps = {}, []
startup_skipped = 0
with open(F) as f:
    r = csv.reader(f); next(r)
    for row in r:
        try:
            ts = float(row[0]); h = row[2]; values = tuple(int(row[i]) for i in (3, 4, 5))
        except (ValueError, IndexError):
            continue
        if h == 'h10' or ts < cut:
            continue
        if ts < cut + STARTUP_SKIP_SECONDS:
            startup_skipped += 1
            continue
        data.setdefault(h, []).append(values); stamps.append(ts)
print(f"File: {F}")
print(f"Startup rows skipped: {startup_skipped} (first {STARTUP_SKIP_SECONDS} seconds after cut)")
print(f"Window: {(max(stamps)-min(stamps))/60:.1f} min\n")
for feature_index, feature in enumerate(('packet_delta', 'distinct_dst_ips', 'distinct_dst_ports')):
    print(f"=== {feature} ===")
    print(f"{'Host':<6}{'N':<7}{'Mean':<10}{'StdDev':<10}{'1xSD':<10}{'2xSD':<10}{'2.5xSD':<10}{'3xSD':<10}{'Thresh(k=2)':<14}{'Thresh(k=2.5)':<16}{'Thresh(k=3)':<14}{'Max'}")
    for h in sorted(data, key=lambda x: int(x[1:])):
        rows_all = data[h]
        # Use packet_delta to select the same active rows for every feature.
        rows = [row for row in rows_all if row[0] > JITTER_MIN_BURST] if h in JITTER_HOSTS else rows_all
        if len(rows) < 2:
            print(f"{h:<6}{len(rows_all):<7}{'(insufficient active cycles)'}")
            continue
        v = [row[feature_index] for row in rows]
        m = statistics.mean(v); s = statistics.stdev(v)
        label = f"{len(v)}/{len(rows_all)}" if h in JITTER_HOSTS else str(len(rows_all))
        print(f"{h:<6}{label:<7}{m:<10.3f}{s:<10.3f}{s:<10.3f}{2*s:<10.3f}{2.5*s:<10.3f}{3*s:<10.3f}{m+2*s:<14.2f}{m+2.5*s:<16.2f}{m+3*s:<14.2f}{max(v)}")
    print()
