#!/usr/bin/env python3
"""
Weekly build of a compact Swiss long-distance timetable for the TravelHub Trains page.

Downloads the official Swiss GTFS (opentransportdata.swiss, ~155 MB zipped / 2 GB unpacked: every train, bus, tram,
boat and cable car), keeps only long-distance and scenic trains (IC, IR, EC, ICE, TGV, Railjet, Nightjet/EN and the
panorama trains such as Glacier Express, Bernina Express, GoldenPass) and writes data/ch_fv.zip -- a small GTFS
with the original trip_ids, so it matches the official GTFS-RT live feed. Standard library only.

Data: opentransportdata.swiss (Swiss open data for public transport), used under its terms of use.
"""
import csv, datetime, io, json, os, sys, urllib.request, zipfile

LONG = {"IC", "ICN", "IR", "EC", "ICE", "TGV", "RJ", "RJX", "NJ", "EN", "PE", "GEX", "BEX", "GPX"}
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "ch_fv.zip")


UA = {"User-Agent": "CosimaTravel-timetable-build/1.0 (+https://cosimatravel.com)"}


def fetch(url, timeout=600):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read(), r.geturl(), r.headers.get("content-type", "")


def candidate_urls(year):
    """Ask the portal's catalogue (CKAN) for the dataset's GTFS zip; fall back to the permalink."""
    urls = []
    for host in ("https://data.opentransportdata.swiss", "https://opentransportdata.swiss"):
        api = f"{host}/api/3/action/package_show?id=timetable-{year}-gtfs2020"
        try:
            body, _, _ = fetch(api, 60)
            res = json.loads(body).get("result", {}).get("resources", [])
            res.sort(key=lambda r: r.get("created") or r.get("last_modified") or "", reverse=True)  # newest first
            for r in res:
                u = r.get("url") or ""
                if u.lower().endswith(".zip") or "zip" in (r.get("format") or "").lower():
                    urls.append(u)
            print(f"catalogue {api}: {len(res)} resources, {len(urls)} zip", flush=True)
            if urls:
                break
        except Exception as e:
            print(f"catalogue {api} failed: {e}", flush=True)
    urls.append(f"https://data.opentransportdata.swiss/dataset/timetable-{year}-gtfs2020/permalink")
    return urls


def download():
    today = datetime.date.today()
    years = [today.year + 1, today.year] if today.month == 12 else [today.year, today.year + 1]
    for y in years:
        for url in candidate_urls(y):
            try:
                print("downloading", url, flush=True)
                data, final, ctype = fetch(url)
                if data[:2] == b"PK":
                    print(f"got {len(data)/1e6:.1f} MB for {y} from {final}", flush=True)
                    return data
                print(f"not a zip ({ctype}, {len(data)} bytes, final URL {final}): {data[:200]!r}", flush=True)
            except Exception as e:  # dataset for that year may not exist yet
                print("failed:", e, flush=True)
    sys.exit("could not download the Swiss GTFS -- see the messages above")


def rows(z, name):
    with z.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig", newline=""))


def panorama_name(first, last):
    ends = (first + " " + last).lower()
    has = lambda *w: any(x in ends for x in w)
    if has("zermatt") or (has("st. moritz", "davos", "chur") and has("brig", "andermatt", "disentis")):
        return "Glacier Express"
    if has("tirano") or (has("lugano") and has("st. moritz", "chur")):
        return "Bernina Express"
    if has("montreux", "zweisimmen") or (has("interlaken") and has("montreux", "gstaad")):
        return "GoldenPass Express"
    if has("luzern") and has("interlaken"):
        return "Luzern–Interlaken Express"
    return "Panorama train"


def main():
    z = zipfile.ZipFile(io.BytesIO(download()))
    routes, cats = {}, {}
    for r in rows(z, "routes.txt"):
        desc = (r.get("route_desc") or "").strip().upper()
        short = (r.get("route_short_name") or "").strip()
        rtype = (r.get("route_type") or "").strip()
        if desc in LONG or rtype in ("101", "102"):
            routes[r["route_id"]] = r
            cats[r["route_id"]] = short if short and short.upper().startswith(desc) and not short.endswith("-Y") else (desc or short)
    print("long-distance routes:", len(routes), flush=True)

    trips = {r["trip_id"]: r for r in rows(z, "trips.txt") if r["route_id"] in routes}
    print("trips:", len(trips), flush=True)

    st_out = io.StringIO()
    st_fields = ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"]
    w = csv.DictWriter(st_out, fieldnames=st_fields, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    used_stops, ends = set(), {}
    for r in rows(z, "stop_times.txt"):
        tid = r["trip_id"]
        if tid not in trips:
            continue
        w.writerow(r)
        used_stops.add(r["stop_id"])
        seq = int(r["stop_sequence"])
        e = ends.setdefault(tid, [seq, r["stop_id"], seq, r["stop_id"]])
        if seq < e[0]: e[0], e[1] = seq, r["stop_id"]
        if seq > e[2]: e[2], e[3] = seq, r["stop_id"]

    stops_out = io.StringIO()
    sw = csv.DictWriter(stops_out, fieldnames=["stop_id", "stop_name", "stop_lat", "stop_lon"], extrasaction="ignore", lineterminator="\n")
    sw.writeheader()
    names = {}
    for r in rows(z, "stops.txt"):
        if r["stop_id"] in used_stops:
            sw.writerow(r)
            names[r["stop_id"]] = r["stop_name"]

    # panorama trains get their marketing name as the "route" so the page can show "Glacier Express 902"
    out_routes, trip_route = {}, {}
    for tid, t in trips.items():
        cat = cats[t["route_id"]]
        if cat.upper() == "PE" and tid in ends:
            cat = panorama_name(names.get(ends[tid][1], ""), names.get(ends[tid][3], ""))
        rid = t["route_id"] + "|" + cat
        out_routes[rid] = {"route_id": rid, "agency_id": routes[t["route_id"]].get("agency_id", ""), "route_short_name": cat, "route_long_name": "", "route_type": "2"}
        trip_route[tid] = rid

    services = {t["service_id"] for t in trips.values()}
    today = datetime.date.today()
    lo, hi = (today - datetime.timedelta(days=2)).strftime("%Y%m%d"), (today + datetime.timedelta(days=21)).strftime("%Y%m%d")

    def table(fields, items):
        b = io.StringIO()
        cw = csv.DictWriter(b, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        cw.writeheader()
        for it in items:
            cw.writerow(it)
        return b.getvalue()

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as o:
        o.writestr("agency.txt", table(["agency_id", "agency_name", "agency_url", "agency_timezone"], rows(z, "agency.txt")))
        o.writestr("routes.txt", table(["route_id", "agency_id", "route_short_name", "route_long_name", "route_type"], out_routes.values()))
        o.writestr("trips.txt", table(["route_id", "service_id", "trip_id", "trip_headsign", "trip_short_name"],
                                      ({**t, "route_id": trip_route[tid]} for tid, t in trips.items())))
        o.writestr("stop_times.txt", st_out.getvalue())
        o.writestr("stops.txt", stops_out.getvalue())
        if "calendar.txt" in z.namelist():
            o.writestr("calendar.txt", table(["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "start_date", "end_date"],
                                             (r for r in rows(z, "calendar.txt") if r["service_id"] in services)))
        o.writestr("calendar_dates.txt", table(["service_id", "date", "exception_type"],
                                               (r for r in rows(z, "calendar_dates.txt") if r["service_id"] in services and lo <= r["date"] <= hi)))
    print(f"wrote {OUT}: {os.path.getsize(OUT)/1e6:.2f} MB, {len(trips)} trips, {len(used_stops)} stops", flush=True)


if __name__ == "__main__":
    main()
