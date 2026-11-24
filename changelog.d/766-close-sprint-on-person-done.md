Fixed

* **A sprint closes when a person moves its last open task to Done (issue #766).** In sprint 6 a person moved every task to Done from the board and the sprint stayed active until it was closed by hand: the review cycle only looked for closing among the sprints holding a blocked task, and a person's move does not close the sprint itself. Each review cycle now checks every active sprint and closes the ones that have settled, with the same retrospective as any other close; a paused, planned or cancelled sprint is left alone.
