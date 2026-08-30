# Yahoo public live mock — monitoring findings — 2026-08-30

Completed in Yahoo's signed-in draft client on 2026-08-30. Room, account,
participant, session, and URL identifiers are intentionally excluded.

## Scope

- Format observed: public standard head-to-head snake mock; 14 teams.
- Purpose: room-entry, active monitoring, queue, and recovery rehearsal.
- League-fit limit: the private league is an eight-team, non-PPR, 16-round
  snake draft with a keeper. This result is not a player-value or roster-build
  recommendation for that league.

## Observations

| Observation | Evidence from the rehearsal | Knowledgebase rule |
| --- | --- | --- |
| Public rooms move quickly | Several picks could complete during one bounded observation window. | Use a 3–5 second cadence inside eight picks and near-continuous fresh checks inside three. |
| Autodraft changes the outcome | The room displayed Autodraft enabled; queue entries were consumed and empty-queue behavior continued the draft. | Keep Autodraft off unless the manager explicitly chooses it as an emergency fallback. |
| Queue controls are state-sensitive | Rows reordered between fresh states, and an intended queue action could target a different visible player. | Derive every queue action from the latest rendered state and verify the queue immediately afterward. |
| The next-available control is not entry proof | The lobby's next-available action did not itself establish the final room state. | Verify the actual room format, assigned slot, and draft-client launch after joining. |
| Filters can update ahead of the list | The position selector reflected a kicker filter before the visible player area had fully caught up. | Verify the selector and the current rows before drafting from a filtered view. |
| Roster count can mask an invalid finish | Yahoo showed a full roster count at completion while a required kicker slot remained empty. | Audit all starter slots, not merely **Draft Complete** or the roster count. |

## Follow-up rehearsal criteria

1. Use an eight-team mock from the private league's actual draft position when
   Yahoo inventory allows.
2. Start with Autodraft off and three acceptable, verified queue players.
3. Record the clock distance, queue contents, and roster-slot audit for every
   user selection.
4. Mark the run complete only when Yahoo's final roster fills every required
   starter slot and its completed-pick state agrees with the log.
