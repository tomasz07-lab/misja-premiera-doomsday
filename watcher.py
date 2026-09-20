#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

CINEMA_ID = "1064"
CINEMA_NAME = "Cinema City Zakopianka"
CINEMA_PAGE = "https://www.cinema-city.pl/kina/Zakopianka/1064"
DOMAIN = "https://www.cinema-city.pl"
LANG = "pl_PL"
TZ = ZoneInfo("Europe/Warsaw")

MOVIE_TERMS = ("avengers", "doomsday")
HORIZON_DAYS = 150
REQUEST_DELAY = 0.20

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/152.0.0.0 Safari/537.36"
)

# Najpierw sprawdzamy najczęściej spotykany identyfikator, potem kilka sąsiednich.
# Po pierwszym udanym wykryciu SITE_ID jest zapisywany w state/seen.json.
SITE_ID_CANDIDATES = [
    "10101", "10102", "10103", "10104", "10105",
    "10106", "10107", "10108", "10109", "10110",
    "10111", "10112", "10113", "10114", "10115",
]


def now_local():
    return datetime.now(TZ)


def normalize(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return value.casefold()


def http_get(url, timeout=30):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def http_json(url):
    last = None
    for attempt in range(3):
        try:
            raw = http_get(url)
            return json.loads(raw.decode("utf-8"))
        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
        ) as exc:
            last = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Nie udało się pobrać JSON: {url}\n{last}")


def api(site_id, path):
    url = f"{DOMAIN}/pl/data-api-service/v1/quickbook/{site_id}{path}"
    payload = http_json(url)
    if "body" not in payload:
        raise RuntimeError(f"Nieoczekiwany format API dla {url}")
    return payload["body"]


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        state = {}
    state.setdefault("site_id", None)
    state.setdefault("seen", {})
    state.setdefault("heartbeat", None)
    state.setdefault("last_status_date", None)
    return state


def save_state(path, state):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")


def try_extract_site_id_from_page():
    try:
        html = http_get(CINEMA_PAGE).decode("utf-8", errors="ignore")
    except Exception:
        return None

    patterns = [
        r"quickbook/(\d{4,8})/",
        r'["\']siteId["\']\s*:\s*["\']?(\d{4,8})',
        r'["\']site_id["\']\s*:\s*["\']?(\d{4,8})',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.I)
        if match:
            return match.group(1)
    return None


def site_id_works(site_id):
    until = (date.today() + timedelta(days=14)).isoformat()
    path = f"/dates/in-cinema/{CINEMA_ID}/until/{until}?attr=&lang={LANG}"
    try:
        body = api(site_id, path)
        return isinstance(body.get("dates"), list)
    except Exception:
        return False


def discover_site_id(state):
    forced = os.environ.get("CINEMA_CITY_SITE_ID")
    if forced:
        print(f"Używam CINEMA_CITY_SITE_ID={forced} z ustawień.")
        return forced

    remembered = state.get("site_id")
    if remembered and site_id_works(str(remembered)):
        print(f"Używam zapamiętanego SITE_ID={remembered}.")
        return str(remembered)

    extracted = try_extract_site_id_from_page()
    if extracted:
        print(f"Strona Zakopianki wskazuje SITE_ID={extracted}.")
        if site_id_works(extracted):
            return extracted

    print("Automatycznie szukam SITE_ID dla polskiego Cinema City…")
    for candidate in SITE_ID_CANDIDATES:
        print(f"  test {candidate}")
        if site_id_works(candidate):
            print(f"Znaleziono działający SITE_ID={candidate}.")
            return candidate
        time.sleep(REQUEST_DELAY)

    raise RuntimeError(
        "Nie udało się automatycznie ustalić polskiego SITE_ID Cinema City. "
        "W logu GitHub Actions będzie widać ten błąd. "
        "Można wtedy ustawić repo variable CINEMA_CITY_SITE_ID ręcznie."
    )


def fetch_dates(site_id):
    until = (date.today() + timedelta(days=HORIZON_DAYS)).isoformat()
    body = api(
        site_id,
        f"/dates/in-cinema/{CINEMA_ID}/until/{until}?attr=&lang={LANG}",
    )
    return body.get("dates", [])


def fetch_day(site_id, day):
    time.sleep(REQUEST_DELAY)
    body = api(
        site_id,
        f"/film-events/in-cinema/{CINEMA_ID}/at-date/{day}?attr=&lang={LANG}",
    )
    films = {str(f["id"]): f for f in body.get("films", [])}
    return films, body.get("events", [])


def is_doomsday(film):
    name = normalize(film.get("name"))
    return all(term in name for term in MOVIE_TERMS)


def normalized_attrs(event):
    return [normalize(x) for x in event.get("attributeIds", [])]


def is_polish_dub(event, film):
    attrs = normalized_attrs(event)

    values = [
        event.get("language"),
        event.get("version"),
        event.get("name"),
        film.get("name"),
    ]

    text = " ".join(
        normalize(v)
        for v in values
        if v
    )

    attr_text = " ".join(attrs)
    combined = f"{attr_text} {text}"

    # Musi to być jakiś rodzaj dubbingu.
    has_dubbing = (
        "dubbed" in combined
        or "dubbing" in combined
        or any("dub" in a for a in attrs)
    )

    if not has_dubbing:
        return False

    # Odrzucamy wyraźnie ukraiński dubbing.
    ukrainian_markers = (
        "ukrainski",
        "ukrainian",
        "dubbing uk",
        "dubbed uk",
        "dub-uk",
        "dub_uk",
    )

    if any(marker in combined for marker in ukrainian_markers):
        return False

    # Jeżeli API jawnie mówi, że to polski dubbing.
    polish_markers = (
        "dubbing pl",
        "dubbed pl",
        "polski",
        "polish",
        "dub-pl",
        "dub_pl",
    )

    if any(marker in combined for marker in polish_markers):
        return True

    # Na polskiej stronie Cinema City zwykły "dubbed" bez oznaczenia
    # innego języka traktujemy jako polski dubbing.
    return True

def is_2d(event):
    attrs = normalized_attrs(event)
    attr_text = " ".join(attrs)

    # Odrzucamy wyraźne 3D i formaty, których użytkownik nie chce.
    blocked = ("3d", "4dx", "imax", "screenx")
    if any(marker in attr_text for marker in blocked):
        return False

    # Jeżeli API jawnie oznacza 2D, mamy pewne dopasowanie.
    if any(a in {"2d", "2-d", "2_d"} or "2d" in a for a in attrs):
        return True

    # W Quickbook zwykłe 2D nie zawsze ma osobny atrybut.
    # Dla Zakopianki przy braku oznaczenia 3D/specjalnego traktujemy seans jako 2D.
    return True


def make_record(event, film):
    attrs = event.get("attributeIds", [])
    presentation = event.get("presentationCode") or event.get("id")
    film_link = film.get("link") or CINEMA_PAGE

    return {
        "id": str(event.get("id")),
        "film": film.get("name", "Avengers: Doomsday"),
        "datetime": event.get("eventDateTime"),
        "auditorium": event.get("auditorium"),
        "attrs": attrs,
        "sold_out": bool(event.get("soldOut")),
        "presentation_code": presentation,
        "film_link": film_link,
    }


def collect(site_id):
    found = {}
    matched_title_debug = []

    dates = fetch_dates(site_id)
    print(f"API zwróciło {len(dates)} dat z repertuarem dla Zakopianki.")

    for day in dates:
        films, events = fetch_day(site_id, day)
        for event in events:
            film = films.get(str(event.get("filmId")), {})
            if not is_doomsday(film):
                continue

            matched_title_debug.append(
                {
                    "date": day,
                    "name": film.get("name"),
                    "attrs": event.get("attributeIds", []),
                    "auditorium": event.get("auditorium"),
                }
            )

            if not is_polish_dub(event, film):
                continue
            if not is_2d(event):
                continue

            record = make_record(event, film)
            if record["id"] and record["datetime"]:
                found[record["id"]] = record

    if matched_title_debug:
        print("Znalezione wpisy Avengers: Doomsday (debug filtrów):")
        for item in matched_title_debug:
            print(json.dumps(item, ensure_ascii=False))

    return found


def format_dt(value):
    try:
        dt = datetime.fromisoformat(value)
        return dt.strftime("%d.%m.%Y • %H:%M")
    except Exception:
        return value or "brak danych"


def send_discord(webhook_url, events, test=False):
    if test:
        payload = {
            "username": "Obserwator",
            "content": "✅ **Obserwator działa!**\nWebhook dla „Misja: Premiera Doomsday” został poprawnie podłączony.",
            "allowed_mentions": {"parse": []},
        }
    else:
        lines = []
        for event in sorted(events, key=lambda x: x["datetime"]):
            sold = " • ⚠️ wyprzedane" if event.get("sold_out") else ""
            room = f" • sala {event['auditorium']}" if event.get("auditorium") else ""
            lines.append(f"**{format_dt(event['datetime'])}**{room}{sold}")

        description = "\n".join(lines[:20])
        if len(events) > 20:
            description += f"\n…i jeszcze {len(events) - 20} seansów."

        link = next((e.get("film_link") for e in events if e.get("film_link")), CINEMA_PAGE)

        payload = {
            "username": "Obserwator",
            "content": "🚨 **MAMY BILETY!**",
            "embeds": [
                {
                    "title": "🎬 Avengers: Doomsday — 2D Dubbing PL",
                    "description": description,
                    "url": link,
                    "fields": [
                        {"name": "Kino", "value": CINEMA_NAME, "inline": False},
                        {
                            "name": "Co zrobić?",
                            "value": "[Otwórz Cinema City Zakopianka](https://www.cinema-city.pl/kina/Zakopianka/1064)",
                            "inline": False,
                        },
                    ],
                    "footer": {
                        "text": f"Sprawdzone {now_local():%d.%m.%Y %H:%M} • Obserwator"
                    },
                }
            ],
            "allowed_mentions": {"parse": []},
        }

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": UA},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status not in (200, 204):
            raise RuntimeError(f"Discord odpowiedział HTTP {resp.status}")

def status_report_due(state):
    now = now_local()

    # Raport wysyłamy dopiero po 08:00 czasu polskiego.
    if now.hour < 8:
        return False

    today = now.date().isoformat()
    return state.get("last_status_date") != today


def send_status_report(webhook_url, current, site_id):
    now = now_local()

    if current:
        ticket_status = f"🎟️ Wykryto **{len(current)}** pasujące seanse."
    else:
        ticket_status = "🔎 Na razie **brak biletów**."

    payload = {
        "username": "Obserwator",
        "embeds": [
            {
                "title": "🟢 Obserwator działa",
                "description": ticket_status,
                "fields": [
                    {
                        "name": "🎬 Film",
                        "value": "Avengers: Doomsday",
                        "inline": True
                    },
                    {
                        "name": "📍 Kino",
                        "value": "Cinema City Zakopianka",
                        "inline": True
                    },
                    {
                        "name": "🎥 Wersja",
                        "value": "2D • Dubbing PL",
                        "inline": True
                    },
                    {
                        "name": "🕐 Ostatnia kontrola",
                        "value": now.strftime("%d.%m.%Y • %H:%M"),
                        "inline": True
                    },
                    {
                        "name": "🌐 Cinema City API",
                        "value": f"Połączenie OK • SITE_ID `{site_id}`",
                        "inline": True
                    },
                    {
                        "name": "🔄 Monitoring",
                        "value": "około co 10 minut",
                        "inline": True
                    }
                ],
                "footer": {
                    "text": "Codzienny raport • Misja: Premiera Doomsday"
                }
            }
        ],
        "allowed_mentions": {
            "parse": []
        }
    }

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": UA
        },
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status not in (200, 204):
            raise RuntimeError(
                f"Discord odpowiedział HTTP {resp.status}"
            )
            
def heartbeat_due(state):
    previous = state.get("heartbeat")
    if not previous:
        return True
    try:
        last = datetime.fromisoformat(previous).date()
        return (date.today() - last).days >= 21
    except Exception:
        return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default="state/seen.json")
    parser.add_argument("--test-discord", action="store_true")
    args = parser.parse_args()

    webhook = os.environ.get("DISCORD_WEBHOOK_URL")

    if not webhook:
        raise SystemExit(
            "Brakuje DISCORD_WEBHOOK_URL. Dodaj webhook jako GitHub Actions secret."
        )

    if args.test_discord:
        send_discord(webhook, [], test=True)
        print("Wysłano testową wiadomość na Discord.")
        return 0

    state = load_state(args.state)
    old_state = json.dumps(state, sort_keys=True)

    site_id = discover_site_id(state)
    state["site_id"] = site_id

    current = collect(site_id)
    seen = state.get("seen", {})

    new_events = [
        event
        for eid, event in current.items()
        if eid not in seen
    ]

    print(
        f"Dopasowane seanse: {len(current)}. "
        f"Nowe względem historii: {len(new_events)}."
    )

    if new_events:
        send_discord(webhook, new_events)
        print("Wysłano alert na Discord.")

        for event in new_events:
            seen[event["id"]] = event

        state["seen"] = seen

    if status_report_due(state):
        send_status_report(webhook, current, site_id)
        state["last_status_date"] = now_local().date().isoformat()
        print("Wysłano dzienny raport kontrolny na Discord.")

    if heartbeat_due(state):
        state["heartbeat"] = now_local().replace(
            microsecond=0
        ).isoformat()

        print(
            "Aktualizuję heartbeat stanu, "
            "aby repo zachowało aktywność."
        )

    new_state = json.dumps(state, sort_keys=True)

    if new_state != old_state:
        save_state(args.state, state)
        print("Stan został zaktualizowany.")
    else:
        print("Stan bez zmian.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
