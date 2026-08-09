# Afon — Voice Recognition Read-Aloud Script (≈ 3 minutes)

**Purpose:** read this aloud once to (a) enroll your voiceprint for speaker biometrics and
(b) calibrate speech-to-text. Read at a natural, unhurried pace — about 140 words per minute.
Sit where you normally talk to Afon, with your usual mic. Don't perform; speak the way you
actually speak. The script is ~430 words and is built to cover a wide range of sounds, numbers,
names, and intonations so the voiceprint is robust.

**How to use it:**
1. `uv sync --extra identity` (one-time, installs the ECAPA backend and microphone capture).
2. `uv run python bench/enroll_voice.py --script "to-read-script.md"` and read each prompted
   block when it says *recording*. (Plain `enroll_voice.py` still works for a quick 12-second
   enrollment; the script gives a stronger profile.)
3. When it finishes, set `AFON_SPEAKER_ID_ENABLED=true` in `.env`.

Pauses are marked with `…` — take a real breath there.

---

## Segment 1 — calibration & wake words (≈ 30 s)

Hey Afon, this is Vazghen, and I'm setting up my voice. … Hey Afon. Alexa. Hey Mycroft.
Hey Rhasspy. … Testing, one, two, three. The quick brown fox jumps over the lazy dog. … I'm
speaking in my normal voice, at my normal pace, in the room where I usually talk to you.

## Segment 2 — phonetic range (≈ 45 s)

She sells sea shells by the shore, while three thin thieves thought through thirty thorny
problems. … Peter Piper picked a peck of pickled peppers; how many peppers did Peter Piper pick?
… Red lorry, yellow lorry. Unique New York. … The fifth sheikh's sixth sheep is sick. …
Crisp, fresh, smooth, rough, bright, dark, warm, cold — vowels and consonants, soft and sharp.

## Segment 3 — numbers, dates, and names (≈ 40 s)

My usual wake-up is six forty-five in the morning. … The meeting is on March the twenty-third
at fourteen hundred hours. … Call plus four-nine, one-seven-two, three-three-three. … Bitcoin
was ninety-eight thousand, two hundred dollars. … Remember the names: Cologne, Yerevan, Gavar,
Armavir, Syunik, and Dvin. … And the people: Anna, Levon, Mikael, and Sarduri.

## Segment 4 — natural conversation (≈ 35 s)

So here's how a normal day sounds. … Afon, what's the weather like today, and do I have
anything on the calendar before noon? … Any unread messages I should know about? … Add a
reminder for tonight — pick up groceries on the way home. … And could you summarise that
article and read me just the headline?

## Segment 5 — intonation & emphasis (≈ 30 s)

Now a few different tones. A question: are you absolutely sure about that? … A command: stop,
wait, and start over. … Surprise: that's incredible, I did not expect that at all! … Calm and
slow: everything is fine, take your time, there's no rush. … That's the whole script — thank
you, Afon.

---

*Total: ~430 words ≈ 3 minutes at a relaxed pace.*
