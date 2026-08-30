# Navigate to a Yahoo Fantasy Football team homepage

This route was verified in the signed-in Yahoo desktop experience on 2026-08-30. It uses visible labels so it remains usable when Yahoo changes season-specific league and team IDs.

## From the Yahoo Fantasy landing page

1. Confirm the Yahoo Sports header shows the profile control instead of **Sign in**.
2. In the fantasy navigation, select **Fantasy Football**.
   - The link targets `https://football.fantasysports.yahoo.com/`.
   - Yahoo may open the football site in another tab. If so, continue in that tab.
3. Confirm the football hub shows **My Teams and Leagues**.
4. Under the current season, locate the desired team. Pre-draft teams appear under **Pre-Draft Teams**; active-season teams may be grouped differently.
5. Select the team name or team logo.
6. Confirm the destination is the team homepage:
   - the page title combines the league and team names;
   - the team name, manager, record, and league context appear near the top;
   - **My Team** is the active team-level destination;
   - the roster and current matchup are visible.

## URL shape

Yahoo team pages use this season-specific shape:

```text
https://football.fantasysports.yahoo.com/f1/<league-id>/<team-id>
```

Do not hard-code or publish the observed IDs. They identify a particular league and team and can change between seasons. For repeatable navigation, start from the football hub and select the team by its visible name. A private browser bookmark is acceptable for convenience, but the hub remains the recovery route when the bookmark becomes stale.

## Navigation once on the team homepage

- **Overview** returns to the football overview for the current league context.
- **League** opens the league homepage.
- **My Team** returns to the team homepage from another league page.
- **Matchups** shows league matchups.
- **Players** opens player search and availability.
- **Research** opens Yahoo's league-aware research tools.
- **Draft** opens draft information and controls for the league.

## Failure and recovery

- If the football hub shows join/create prompts but no expected teams, verify that the correct Yahoo identity is active and that the desired league belongs to the current season.
- If Yahoo shows **Sign in**, repeat the [login procedure](yahoo-login.md) in the same persistent browser profile.
- If more than one team is listed, use both the team and league names to choose the intended homepage.
