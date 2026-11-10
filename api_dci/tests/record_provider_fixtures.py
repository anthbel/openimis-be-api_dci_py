"""Record providers used by the api_dci tests, reached by dotted path."""

PROVIDER_RIGHT = "990001"

HOUSEHOLDS = [{"id": f"hh-{n}", "seq": n} for n in range(1, 6)]


class FakeHouseholdProvider:
    """Serves HOUSEHOLDS in seq order; the cursor is the last seq served, as a string."""

    calls = []

    def required_right(self):
        return PROVIDER_RIGHT

    def search(self, user, query, cursor, limit):
        FakeHouseholdProvider.calls.append(
            {"user": user, "query": query, "cursor": cursor, "limit": limit}
        )
        after = int(cursor) if cursor else 0
        rows = [h for h in HOUSEHOLDS if h["seq"] > after][:limit]
        has_more = bool(rows) and rows[-1]["seq"] < HOUSEHOLDS[-1]["seq"]
        return rows, (str(rows[-1]["seq"]) if has_more else None)


class ProviderWithoutSearch:
    def required_right(self):
        return PROVIDER_RIGHT


NOT_A_PROVIDER = object()
