# emergency.md — who Afon reaches when something is actually wrong (35.F2)

Copy this to `emergency.md` (gitignored, like `contacts.md`) and edit it. Afon reads it from disk
at the moment it is needed, so an edit takes effect immediately and a process that has been up for
weeks cannot be working from a stale list.

One line per category. The rungs are tried **in the order written**, and Afon says afterwards which
ones he reached and which he did not — he never claims one he could not.

    category: action target (name), action target (name), ...

Actions: `push` (your phone, via ntfy), `call`, `telegram`, `signal`, `email`. `push` needs no
target. Categories: `medical`, `fire`, `intruder`, `accident`, `unknown`, and `default` for
anything not listed.

    medical:  call +490000000000 (next of kin), telegram @nextofkin, push
    fire:     call 112 (fire service), push
    intruder: call 110 (police), push
    accident: call +490000000000 (next of kin), push
    default:  push

Two things worth knowing before you rely on it:

* The classifier is **biased to silence**. It fires only on unambiguous phrasing said in the
  present tense, and it is guarded against figures of speech, procedure questions and narration —
  "this bug is killing me" and "what do I do if there's a fire" both do nothing. The cost of that
  bias is that an emergency phrased unusually will not trip it.
* Which is why there is a manual phrase, and why it bypasses every guard: **"Afon, emergency"**.
  It always works. If you remember one thing, remember that one.
