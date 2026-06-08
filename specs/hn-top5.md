# Hacker News — top 5 posts

Run against the real site:
    MODEL_REPO=mlx-community/UI-Venus-1.5-8B-6bit BASE_URL=https://news.ycombinator.com \
      HEADED=1 ./run.sh specs/hn-top5.md

## Top stories are visible
- goto /
- verify the orange "Hacker News" logo is visible in the top bar
- verify a numbered list of story headlines is visible
- verify at least five story headlines are visible on the page
- screenshot

## Open the top post
- goto /
- click the headline of the first (top-ranked) story
- verify the page navigated away from the Hacker News front page
- screenshot
