# ServiceNow with agent-browser

## URLs

- A list: `https://<instance>.service-now.com/nav_to.do?uri=incident_list.do`
- A record by number: `https://<instance>.service-now.com/nav_to.do?uri=incident.do?sysparm_query=number=INC0010023`
- Other tables follow the same pattern: `change_request_list.do`, `sc_req_item.do`, `sn_vul_vulnerable_item_list.do`.

## The frame

The classic UI renders the page inside an iframe named `gsft_main`. In a snapshot it appears as
`- iframe "gsft_main" [ref=e7] (children f2e...):` and every element inside carries that prefix, for example
`- button "Update" [ref=f2e3]`. Use those refs exactly like `e12` refs. When the frame navigates (opening a record,
clicking Update), refs inside it change: read the new snapshot the action returned.

The new UI creates `gsft_main` after the page's own load event. If the snapshot from `goto` shows no iframe line,
run `agent-browser wait --text "<a label you expect on the form>"` and use the snapshot it returns.

## Recipe: update an incident

1. `agent-browser goto https://<instance>.service-now.com/nav_to.do?uri=incident.do?sysparm_query=number=INC0010023`
2. If `sign_in_suspected` is true, ask the human to sign in, then `agent-browser wait --signed-in <instance>.service-now.com`.
3. `agent-browser snapshot --find "Work notes"`. If nothing matches, form sections are tabs: find `- tab "Notes"` in a plain
   `agent-browser snapshot`, click its ref, then search again.
4. `agent-browser fill f2e20 "Rebooted the mail server; monitoring."`
5. Choice lists (State, Priority, Category) are `combobox` lines: `agent-browser select f2e14 "2 - High"`.
   The label must match one of `details.options` exactly; the error lists them.
6. Reference fields (Assigned to, Caller) are typeahead: `agent-browser type f2e10 "Abel Tuter"`, then
   `agent-browser snapshot --find "Abel"` and click the matching option line.
7. Click the `Update` button: `agent-browser click f2e3`. The result has `navigated: true` and a fresh snapshot.
8. Confirm the record: `agent-browser text` or `agent-browser snapshot --find INC0010023`.

## Lists

List views are large. Use `agent-browser snapshot --find "INC0010023"` to locate a row, then click the number link.
Filters live in the breadcrumb and the filter navigator; the search box is `searchbox "Search"`.

## Dialogs and unsaved changes

- A confirm dialog (for example on Delete or Resolve) makes the click fail with `dialog`; re-run the click with
  `--accept-dialog` only when the user wants that action.
- Leaving a form with edits fails with `unsaved_changes`. Click Update or Save first; use `--discard-changes` only if
  the user says the edits can be dropped.

## Exports

"Export as CSV" from a list context menu starts a download. Run `agent-browser downloads` afterwards to save it under the
profile's downloads folder and get the path.
