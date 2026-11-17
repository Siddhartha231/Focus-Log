# How sync works

FocusLog is offline-first. The timer always writes to the local SQLite file,
with or without a network. Sync is a separate step (`focuslog sync`) that makes
the server and the local file agree.

## The pieces

**Every session has a uuid made on the device.** The server never invents ids.
That means uploading the same session twice can't create a duplicate, so the
client is free to retry whenever a request fails or a response gets lost.

**Local rows have a `dirty` flag.** Adding or deleting a session sets
`dirty = 1`. Push sends the dirty rows and clears the flag when the server
answers.

**The server numbers every change.** Each user has a counter (`change_seq`).
Every change the server accepts gets the next number, stored on the row as
`seq`. A device remembers the last number it saw (its *cursor*) and asks for
"everything after N". Nothing has to compare clocks to know what's new.

**Deletes are soft.** A deleted session stays in the table with `deleted = 1`
(a tombstone). If the row just disappeared, other devices would never find out
it was deleted.

## One sync

```
focuslog sync
  1. push:  POST /v1/sync/push   {sessions: [all dirty rows]}     (batches of 200)
  2. pull:  GET  /v1/sync/pull?since=<cursor>                     (pages of 500)
            apply each row, save the new cursor
```

The pull applies a page and saves the cursor in a single SQLite transaction.
If the program dies halfway, either both happened or neither did, so the cursor
can never get ahead of the data.

## Conflicts

Two devices can change the same session while offline (for now that means one
of them deleted it). The rule is **last write wins**: every row carries
`updated_at` (milliseconds, set by the device that made the change), and a
change only replaces a row if its `updated_at` is newer. The server applies
that rule on push, and the client applies the same rule on pull, so both sides
end up with the same answer no matter what order the devices sync in.

## Two devices pushing at the same time

Push locks the user's row (`SELECT ... FOR UPDATE`) for the whole transaction.
Without the lock, two pushes can both read `change_seq = 5` and both hand out
6, 7, 8..., and a device that already pulled up to 8 would skip the other
push's changes forever. `test_two_devices_pushing_at_once_get_distinct_seq_numbers`
runs 8 pushes in parallel against Postgres to check this. (I tried removing the
lock: the test fails with lost changes.)

## Accounts

- Passwords are hashed with scrypt and a random salt per password.
- Logging in creates a random token for that device. The server stores only a
  SHA-256 hash of it. `focuslog logout` deletes it on the server.
- The client keeps the token in `~/.focuslog/config.json`, readable only by you.
- A wrong password and an unknown email return the same error, so the API
  doesn't reveal who has an account.

## Known limits

- Last write wins trusts device clocks. A device with a badly wrong clock can
  win a conflict it should have lost. Fine for one person's study log; a shared
  app would want server-side versions instead.
- Session times are saved as local wall-clock time with no time zone. Studying
  at 9am shows as 9am everywhere, which is what I want from a study log, but it
  isn't a precise instant.
- Tokens don't expire on their own, and there is no rate limiting on login yet.
- Goals are local to each device for now.
- The server creates its tables on startup. A schema change later would need
  real migrations (Alembic).
- SQLite works for the server in development, but the row lock above only
  exists on Postgres, so production should use Postgres.
