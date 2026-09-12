"""Sources that cannot lawfully or technically be read by a machine.

These are NOT pretend scanners. Each one reports its real status, links to the
real place a human has to look, and raises a MANUAL VERIFICATION REQUIRED task
that Topher can complete and attach evidence to (spec 16 / 87).
"""
from __future__ import annotations

from ..http import Blocked, get
from .base import (BLOCKED, MANUAL, MANUAL_ONLY, OK, UNAVAILABLE,
                   PropertySource, SourceResult, register)


class ManualSource(PropertySource):
    """Base for human-in-the-loop sources. Still does a real reachability probe."""
    access = MANUAL
    probe_url = ""
    priority = 2

    def manual_task(self, prop_id=None):
        t = super().manual_task(prop_id)
        t["priority"] = self.priority
        return t

    def health_check(self) -> SourceResult:
        url = self.probe_url or self.url
        if not url:
            return SourceResult(status=MANUAL_ONLY, detail=self.why_manual)
        try:
            r = get(url, timeout=25)
            return SourceResult(
                status=MANUAL_ONLY,
                detail=f"page reachable (HTTP {r.status}); not machine readable")
        except Blocked as exc:
            return SourceResult(status=MANUAL_ONLY, error=str(exc),
                                detail="the operator blocks automated access; "
                                       "not bypassed - use the site by hand")
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc),
                                detail=f"site did not answer: {exc}")


class GarlandAssessor(ManualSource):
    name = "garland_assessor"
    label = "Garland County Assessor (actDataScout)"
    kind = "assessment"
    url = "https://www.actdatascout.com/RealProperty/Arkansas/Garland"
    probe_url = url
    access = BLOCKED
    why_manual = ("actDataScout answers automated requests with HTTP 403. We do not "
                  "work around that. The same underlying tax-roll data reaches us "
                  "through the Arkansas GIS Office parcel layer, but the Assessor's "
                  "own site is the place to confirm current owner, values, sketch, "
                  "photos and the official parcel-type code table.")
    what_to_check = ("Open the parcel on actDataScout and confirm: current owner, "
                     "current assessed / land / improvement values, heated square "
                     "footage, year built, and whether the improvement record matches "
                     "what is actually standing there.")


class GarlandTaxCollector(ManualSource):
    name = "garland_tax_collector"
    label = "Garland County Tax Collector - delinquent taxes"
    kind = "tax"
    url = "https://www.arkansastaxsearch.com/garland.html"
    probe_url = "https://www.garlandcounty.org/181/Tax-Collector"
    why_manual = ("The Collector's inquiry portal (arkansastaxsearch.com) is a login-gated "
                  "session application, and the assessor portal (ARCountyData) sits behind "
                  "a browser challenge. Neither is a data feed and neither is bypassed. "
                  "Tax status changes the whole picture of a deal, so it is checked by "
                  "hand rather than guessed.")
    what_to_check = ("Look up the parcel and record: are taxes current, how many years "
                     "are delinquent, the dollar amount owed, and whether the parcel has "
                     "been certified to the Commissioner of State Lands.")


class CommissionerOfStateLands(ManualSource):
    name = "cosl"
    label = "Arkansas Commissioner of State Lands (COSL)"
    kind = "tax_sale"
    url = "https://www.cosl.org/"
    probe_url = "https://www.cosl.org/"
    why_manual = ("COSL publishes certified-delinquent parcels and auction results "
                  "through an interactive catalogue and a separate auction site "
                  "(auction.cosl.org) that require a browser session. There is no "
                  "public data feed, so we link straight to the right place instead "
                  "of inventing a status.")
    what_to_check = ("Search the parcel at cosl.org and auction.cosl.org and record: "
                     "is it certified to the State, is it scheduled for auction, has it "
                     "already been auctioned, is it available post-auction, and what is "
                     "the redemption position. IMPORTANT: paying somebody else's "
                     "delinquent taxes does NOT make you the owner in Arkansas - only "
                     "a completed purchase from the Commissioner and a limited warranty "
                     "deed does, and even then verify with an attorney.")


class HotSpringsVacantStructures(ManualSource):
    name = "hs_vacant_structures"
    label = "City of Hot Springs - confirm vacancy / condemnation with the office"
    kind = "vacancy"
    url = "https://www.hotspringsar.gov/153/Planning-Development"
    probe_url = url
    priority = 3
    why_manual = ("The City's vacant-structure register is read automatically from its GIS "
                  "(hs_gis_vacant). What the map cannot say is whether a condemnation or "
                  "demolition order is pending - only the office can.")
    what_to_check = ("Ask Planning & Development / Code Enforcement whether this address "
                     "has a condemnation or demolition order, and what would clear it.")


class HotSpringsCodeEnforcement(ManualSource):
    name = "hs_code_enforcement"
    label = "City of Hot Springs - confirm lien payoff & older code history"
    kind = "code"
    url = "https://www.hotspringsar.gov/153/Planning-Development"
    probe_url = url
    priority = 3
    why_manual = ("City housing liens and 2025 code cases are read automatically from the "
                  "City GIS (hs_gis_liens, hs_gis_code_cases). The map does not carry the "
                  "current payoff figure with interest, or cases before 2025.")
    what_to_check = ("Ask for the lien payoff amount as of today and the full code history. "
                     "A cleanup lien is money the City already spent on the lot - it has to "
                     "be paid or negotiated before a clean transfer.")


class GarlandRecorder(ManualSource):
    name = "garland_recorder"
    label = "Garland County Circuit Clerk / Recorder - deeds & liens"
    kind = "title"
    url = "https://www.garlandcounty.org/166/Circuit-Clerk"
    probe_url = url
    why_manual = ("Recorded documents are the legal truth about ownership and "
                  "encumbrances. They are indexed for human search, and a title read is "
                  "a judgement call - this is exactly the place not to guess.")
    what_to_check = ("Pull the chain of title and every recorded instrument against the "
                     "parcel: deeds, mortgages, judgements, liens, easements, and any "
                     "probate. Then have an Arkansas real-estate attorney read it.")


class HotSpringsZoning(ManualSource):
    name = "hs_planning_zoning"
    label = "City of Hot Springs - Planning & Zoning (confirm the use)"
    kind = "zoning"
    url = "https://www.hotspringsar.gov/153/Planning-Development"
    probe_url = url
    priority = 3
    why_manual = ("The zoning district is read automatically from the City GIS "
                  "(hs_gis_zoning). Whether YOUR intended use is permitted by right, needs "
                  "a conditional-use permit, or is not allowed is decided by Planning, not "
                  "by a map. Assuming approval is how people lose money.")
    what_to_check = ("Tell Planning the district shown here and ask whether your intended "
                     "use (rental, storage, workshop, 3D printing, retail, seasonal food "
                     "stand) is permitted by right, conditional, or not permitted.")


class PublicListings(ManualSource):
    name = "public_listings"
    label = "Public for-sale listings (MLS / portals)"
    kind = "listing"
    url = "https://www.har.com/"
    probe_url = ""
    why_manual = ("The major listing portals prohibit automated collection in their "
                  "terms of service, and MLS data needs a licensed feed. We do not "
                  "scrape them. Enter a price by hand and it becomes evidence with your "
                  "name on it.")
    what_to_check = ("If this property is listed, record the asking price, days on "
                     "market, listing agent and any price reductions, and attach the "
                     "listing link as evidence.")


MANUAL_SOURCES = [
    register(GarlandAssessor()),
    register(GarlandTaxCollector()),
    register(CommissionerOfStateLands()),
    register(HotSpringsVacantStructures()),
    register(HotSpringsCodeEnforcement()),
    register(GarlandRecorder()),
    register(HotSpringsZoning()),
    register(PublicListings()),
]
