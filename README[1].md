# Misja: Premiera Doomsday

Watcher sprawdza Cinema City Zakopianka i wysyła powiadomienie na Discord, gdy pojawi się:

- **Avengers: Doomsday**
- **Cinema City Zakopianka**
- **2D**
- **Dubbing PL**

## Konfiguracja GitHub

1. Utwórz **publiczne** repozytorium, np. `misja-premiera-doomsday`.
2. Wgraj do niego całą zawartość tego folderu.
3. Wejdź w:
   `Settings → Secrets and variables → Actions → Secrets → New repository secret`
4. Nazwa sekretu:
   `DISCORD_WEBHOOK_URL`
5. Wartość:
   Twój URL webhooka Discord „Obserwator”.
6. Wejdź w kartę `Actions`.
7. Otwórz workflow `Doomsday Watcher`.
8. Kliknij `Run workflow`, zaznacz `test_discord = true` i uruchom.
9. Jeżeli na kanale `#mamy-bilety` pojawi się test, uruchom workflow ponownie bez testu.

## Jak często działa

Workflow uruchamia się co 10 minut.

## Ważne

Webhook Discorda jest przechowywany jako GitHub Secret. Nie wpisuj go do plików repozytorium.

Skrypt próbuje automatycznie wykryć polski `SITE_ID` Quickbook API Cinema City.
Po wykryciu zapisuje go do `state/seen.json`, dzięki czemu kolejne uruchomienia nie muszą szukać go ponownie.

Jeżeli autodetekcja kiedyś przestanie działać, możesz dodać:
`Settings → Secrets and variables → Actions → Variables`
i utworzyć zmienną `CINEMA_CITY_SITE_ID`.

## Stan

`state/seen.json` zapisuje:
- wykryty `site_id`,
- identyfikatory seansów, o których powiadomienie już zostało wysłane,
- heartbeat aktualizowany co 21 dni.
