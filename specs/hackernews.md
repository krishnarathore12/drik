# Hacker News — open the top story and scroll

Run it:
    BASE_URL=https://news.ycombinator.com ./run.sh specs/hackernews.md
Watch it live:
    HEADED=1 BASE_URL=https://news.ycombinator.com ./run.sh specs/hackernews.md

Clicks the top story's headline (a large, reliable target) which opens the linked
article, confirms we navigated away from the front page, then scrolls down the
article.

## Open the top story and scroll down
- goto /
- verify the orange "Hacker News" header bar is visible
- verify a numbered list of news story headlines is visible
- click the headline of the very first story at the top of the list
- verify not a numbered list of Hacker News front-page stories is shown
- scroll down
- scroll down
- verify not a numbered list of Hacker News front-page stories is shown
