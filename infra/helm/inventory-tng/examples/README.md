# Starting points for a `my-values.yaml`

[Installing](../../../../docs/deployment.md#deploying-to-kubernetes) tells you to keep your
own values file and pass it with `--values`, and says why `--set` flags are the
arrangement that silently comes undone. These are what that file starts as.

Copy one, rename it, and keep it wherever you keep deployment configuration.
Nothing here is applied automatically and the chart does not read this
directory — a file you have not passed to `--values` has no effect at all.

| File | For | Second factor | Volunteer roster |
| --- | --- | --- | --- |
| [`onboarding.yaml`](onboarding.yaml) | A deployed development environment, a demo, anywhere people are being shown the app for the first time | not required | published |
| [`real-data.yaml`](real-data.yaml) | Staging, QA, and production — anywhere the inventory in it is the organisation's actual inventory | required | withheld |

The last column is the one to read twice, and
[Publishing the volunteer roster](../../../../docs/deployment.md#publishing-the-volunteer-roster)
is why: the left-hand file rests on nobody in it being a real person.

## Why two files and not four

Staging, QA and production would be identical files. The question these answer
is not which tier of environment this is, it is **whether the people and the
stock in it are real** — and that has two answers, not four. A staging
environment holding a copy of real stock takes `real-data.yaml`; a staging
environment that exists to demonstrate the app to a room takes
`onboarding.yaml`. The file is named for the answer so that choosing it is
choosing something.

Both settings above turn on that one question, from opposite directions: an
authenticator is friction worth paying for an account that guards something
real, and a roster is safe to hand out only while nobody in it exists.

Those two settings are the whole of what the files disagree about. Everything
else is a placeholder you must change in whichever you copy —
`django.labelBaseUrl` especially, for the reason
[Installing](../../../../docs/deployment.md#deploying-to-kubernetes) gives.
