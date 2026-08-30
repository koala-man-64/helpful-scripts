# Yahoo Fantasy Football league scoring and settings

Captured from the signed-in Yahoo desktop experience on 2026-08-30. The authoritative source is the league's **Scoring & Settings** page, not the abbreviated stat columns shown on team or player pages.

## Where Yahoo stores the rules

1. Open the desired Yahoo Fantasy Football team homepage.
2. Select **League** in the main fantasy navigation.
3. On the league homepage, select **Settings**.
4. Confirm the destination heading is **Scoring & Settings**.

The canonical URL has this season-specific shape:

```text
https://football.fantasysports.yahoo.com/f1/<league-id>/settings
```

Do not hard-code or publish the observed league ID. Use the visible navigation path so the procedure survives a new season or league. A team may also receive a **Confirm League Settings** link whose URL includes both league and team IDs; it displays the same settings, but the league-level URL above is the canonical reference.

## Draft-relevant summary

- 10-team, head-to-head keeper league.
- Live standard draft with 75 seconds per pick.
- Starting lineup: 1 QB, 3 WR, 2 RB, 1 TE, 1 WR/RB/TE flex, 1 K, and 1 DEF.
- Bench: 6 spots. Injured reserve: 1 spot.
- Fractional and negative points are enabled.
- The scoring table contains no reception-points category, so this is non-PPR scoring.
- Yardage bonuses apply at 350 passing yards and 150 rushing or receiving yards.
- Short field goals are discounted versus Yahoo defaults: 1 point from 0-19 yards and 2 points from 20-29 yards.
- Waivers use a continual rolling list with Game Time-Tuesday weekly waivers and a two-day waiver period.
- There are no season or weekly acquisition limits and no season trade limit.
- Six teams make the playoffs, played in Weeks 15-17 with reseeding enabled.

## Complete league settings

League ID, league name, and logo are deliberately omitted because they identify the private league. All other rows visible in the settings table are recorded below.

| Setting | League value |
| --- | --- |
| Auto-renew enabled | Yes |
| Draft type | Live Standard Draft |
| Draft time | Sun Aug 30, 4:30 p.m. PDT |
| Cash league | Not a cash league |
| Maximum teams | 10 |
| Live draft pick time | 1 minute, 15 seconds |
| Keeper settings | Yes; Keeper League Management tools enabled |
| Keeper deadline | Thu Aug 27, 12:00 a.m. PDT |
| Scoring type | Head-to-Head |
| Scoring starts | Week 1 |
| Can't Cut List provider | None |
| Maximum acquisitions for entire season | No maximum |
| Maximum acquisitions per week | No maximum |
| Maximum trades for entire season | No maximum |
| Trade end date | November 28, 2026 |
| Draft-pick trades | Allowed |
| Trade review | League Votes |
| Votes required to veto | Default |
| Trade reject time | 1 day |
| Waiver time | 2 days |
| Waiver type | Continual rolling list |
| Weekly waivers | Game Time - Tuesday |
| Add injured waiver/free-agent players directly to injury slot | No |
| Post-draft players | Free Agents |
| Playoffs | 6 teams; Weeks 15, 16, and 17; ends Monday, Jan 4 |
| Playoff tie-breaker | Best regular-season record versus opponent wins |
| Playoff reseeding | Yes |
| Divisions | No |
| Lock eliminated teams | Yes |
| Play against median score | No |
| Play against a second opponent | No |
| Apply injured status for postponed games | Yes |
| Roster positions | QB, WR, WR, WR, RB, RB, TE, W/R/T, K, DEF, BN, BN, BN, BN, BN, BN, IR |
| Fractional points | Yes |
| Negative points | Yes |
| Lock benched players | No |
| League publicly viewable | No |
| Invite permissions | Commissioner Only |

## Complete scoring rules

### Offense

| Category | League value |
| --- | --- |
| Passing yards | 1 point per 25 yards; 3-point bonus at 350 yards |
| Passing touchdowns | 4 points |
| Interceptions | -1 point |
| Rushing yards | 1 point per 10 yards; 2-point bonus at 150 yards |
| Rushing touchdowns | 6 points |
| Receiving yards | 1 point per 10 yards; 2-point bonus at 150 yards |
| Receiving touchdowns | 6 points |
| Return touchdowns | 6 points |
| 2-point conversions | 2 points |
| Fumbles lost | -2 points |
| Offensive fumble return touchdown | 6 points |

Yahoo does not list receptions as a scoring category for this league. Targets and receptions may appear as informational stats, but they do not directly earn fantasy points.

### Kickers

| Category | League value | Yahoo default shown |
| --- | ---: | ---: |
| Field goals, 0-19 yards | 1 | 3 |
| Field goals, 20-29 yards | 2 | 3 |
| Field goals, 30-39 yards | 3 | Not shown as different |
| Field goals, 40-49 yards | 4 | Not shown as different |
| Field goals, 50+ yards | 5 | Not shown as different |
| Point after attempt made | 1 | Not shown as different |

The page explicitly marks only the 0-19 and 20-29 yard values as differing from Yahoo defaults.

### Defense and special teams

| Category | League value |
| --- | ---: |
| Sack | 1 |
| Interception | 2 |
| Fumble recovery | 2 |
| Touchdown | 6 |
| Safety | 2 |
| Blocked kick | 2 |
| Kickoff and punt return touchdowns | 6 |
| Points allowed: 0 | 10 |
| Points allowed: 1-6 | 7 |
| Points allowed: 7-13 | 4 |
| Points allowed: 14-20 | 1 |
| Points allowed: 21-27 | 0 |
| Points allowed: 28-34 | -1 |
| Points allowed: 35+ | -4 |
| Extra point returned | 2 |

## Refresh procedure

Settings can change, especially before the draft. Before using these rules for rankings or draft tools:

1. Reopen **League → Settings**.
2. Confirm the **Scoring & Settings** heading and current season context.
3. Compare both tables with this snapshot, paying particular attention to roster positions, scoring type, yardage bonuses, PPR categories, waiver rules, and draft timing.
4. Record the new access date and update changed values without adding league or account identifiers.

