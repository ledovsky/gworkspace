from googleapiclient.discovery import build


def _extract(person):
    names = person.get("names", [])
    emails = person.get("emailAddresses", [])
    name = names[0].get("displayName", "") if names else ""
    return [(name, e.get("value")) for e in emails if e.get("value")]


def people_search(creds, query: str):
    svc = build("people", "v1", credentials=creds)

    # Prime contacts cache — searchContacts requires this before it returns results.
    svc.people().searchContacts(query="", readMask="names,emailAddresses").execute()

    contacts = svc.people().searchContacts(
        query=query,
        readMask="names,emailAddresses",
        pageSize=10,
    ).execute().get("results", [])

    directory = svc.people().searchDirectoryPeople(
        query=query,
        readMask="names,emailAddresses",
        sources=["DIRECTORY_SOURCE_TYPE_DOMAIN_PROFILE",
                 "DIRECTORY_SOURCE_TYPE_DOMAIN_CONTACT"],
        pageSize=10,
    ).execute().get("people", [])

    rows = []
    seen = set()
    for c in contacts:
        for name, email in _extract(c.get("person", {})):
            if email not in seen:
                seen.add(email)
                rows.append((name, email, "contact"))
    for p in directory:
        for name, email in _extract(p):
            if email not in seen:
                seen.add(email)
                rows.append((name, email, "directory"))

    if not rows:
        print(f"No people found for '{query}'.")
        return
    for name, email, source in rows:
        print(f"{name} — {email}  [{source}]")
