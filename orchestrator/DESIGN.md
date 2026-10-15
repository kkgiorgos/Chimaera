# Dashboard design

The machine allocation view is the focal point: available slots and actual case
assignments make the shared queue visible. Counts and ETA support that view rather
than replacing it with a wall of statistics. Outlines distinguish an available
slot from an occupied one; color always accompanies a written status.

| Token | Mocha / Sky | Latte / Maroon |
| --- | --- | --- |
| Canvas | `#1e1e2e` | `#eff1f5` |
| Navigation and machine surface | `#181825` | `#e6e9ef` |
| Primary text | `#cdd6f4` | `#4c4f69` |
| Secondary text | `#a6adc8` | `#6c6f85` |
| Boundary | `#45475a` | `#bcc0cc` |
| Action | `#89dceb` | `#e64553` |

IBM Plex Sans carries the interface, with 29 px page headings, 18 px section
headings, and 12–14 px operational text. IBM Plex Mono is reserved for JSON and
logs. Dark text on the light theme's maroon buttons meets normal-text contrast;
the accent color itself stays unchanged. Fonts, Monaco, workers, and palette data
are bundled locally.

Content is left aligned. The desktop arrangement keeps machine capacity, the
selected experiment, and its case table in one workspace:

```text
┌──────────────────┬────────────────────────────────────────────────────┐
│ Chimaera         │ Machines & workers               New experiment    │
│                  ├────────────────┬────────────────┬──────────────────┤
│ Machines         │ Machine A      │ Machine B      │ Machine C        │
│ Experiments      │ case / slot    │ case / slot    │ case / slot      │
│ Notifications    │ CPU, RAM, ETA  │ CPU, RAM, ETA  │ CPU, RAM, ETA    │
│                  ├────────────────┴────────────────┴──────────────────┤
│ Experiment queue │ Selected experiment     completion / ETA / stop    │
│                  │ Case   pattern   pressure   machine   state   logs │
│                  │                                                    │
│ Appearance       │ Activity                                           │
└──────────────────┴────────────────────────────────────────────────────┘
```

The editor is a focused dialog: JSON on the left, machine selection and expanded
cases on the right. Logs use a separate scrollable dialog. Mobile moves navigation
above the content, stacks machines, and scrolls the case table within its own
bounds. Dialogs use native focus trapping and Escape handling.

There are no decorative gradients, automatic entrances, or ornamental charts.
Unknown ETA, stale progress, pending stop, and incomplete collection remain
explicit states. Controls describe the action they perform. Destructive actions
require a concrete confirmation, while ordinary configuration and queueing do not.
