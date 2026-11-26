"""Hand-curated, individually sourced numeric trivia questions for
trivia_game.py's sudden-death tiebreaker.

Kept in their own file, separate from trivia_game.py's actual game
logic, purely because there are 50 of them and they'd otherwise dwarf
everything else in that module - same split cauldron's own
trivia_tiebreakers.py uses. See NumericTiebreaker and
trivia_game._play_tiebreaker() for how these get used.

Researched via live web search (Wikipedia, Box Office Mojo, Guinness
World Records, Billboard, and similar primary/authoritative sources),
not from memory - same rigor cauldron's own bank was built with. All
dollar figures are original, non-inflation-adjusted amounts; a handful
of box-office totals are explicitly noted as "as of" a given year since
a future theatrical re-release could still nudge a lifetime gross
upward - worth a quick re-check against the cited source before reusing
this bank many years from now, same caveat cauldron's own file carries.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NumericTiebreaker:
    """A sudden-death question with a single, verifiable numeric answer --
    both teams guess at once (see trivia_game._play_tiebreaker()), and
    whichever guess is closer wins outright. ``source`` is shown
    alongside the real answer once the tiebreaker resolves, so the room
    can see it's not made up."""

    question: str
    answer: float
    source: str


TIEBREAKER_QUESTIONS: tuple[NumericTiebreaker, ...] = (
    # --- Movies & TV specials ---
    NumericTiebreaker(
        question="What was Home Alone's worldwide box office total, in millions of dollars?",
        answer=476.7,
        source="Wikipedia: Home Alone (1990 film), corroborated by Box Office Mojo "
        "- lifetime total as of 2026.",
    ),
    NumericTiebreaker(
        question="What was Elf's production budget, in millions of dollars?",
        answer=33.0,
        source="Wikipedia: Elf (film).",
    ),
    NumericTiebreaker(
        question="What was The Santa Clause's worldwide box office total, in millions of dollars?",
        answer=190.5,
        source="Wikipedia: The Santa Clause, corroborated by Box Office Mojo - "
        "lifetime total as of 2026, includes some re-release grosses.",
    ),
    NumericTiebreaker(
        question="What is the runtime of Die Hard, in minutes?",
        answer=132.0,
        source="Wikipedia: Die Hard.",
    ),
    NumericTiebreaker(
        question="What is the runtime of It's a Wonderful Life, in minutes?",
        answer=131.0,
        source="Wikipedia: It's a Wonderful Life.",
    ),
    NumericTiebreaker(
        question="What was A Christmas Story's original 1983 domestic box office "
        "total, in millions of dollars?",
        answer=19.3,
        source="Box Office Mojo - original 1983 theatrical release only, not "
        "counting later re-releases.",
    ),
    NumericTiebreaker(
        question="What was National Lampoon's Christmas Vacation's original 1989 "
        "domestic box office total, in millions of dollars?",
        answer=71.3,
        source="Box Office Mojo - original 1989 release only, not counting later "
        "re-releases.",
    ),
    NumericTiebreaker(
        question="What was the production budget of the 2000 live-action How the "
        "Grinch Stole Christmas, in millions of dollars?",
        answer=123.0,
        source="Wikipedia: How the Grinch Stole Christmas (2000 film).",
    ),
    NumericTiebreaker(
        question="What is the runtime of the original 1966 animated How the Grinch "
        "Stole Christmas! TV special, in minutes?",
        answer=24.0,
        source="Wikipedia: How the Grinch Stole Christmas! (TV special).",
    ),
    NumericTiebreaker(
        question="What was the production budget of The Polar Express, in millions "
        "of dollars?",
        answer=170.0,
        source="The-Numbers.com: The Polar Express (2004).",
    ),
    NumericTiebreaker(
        question="What is the runtime of the original 1964 Rudolph the Red-Nosed "
        "Reindeer TV special, in minutes?",
        answer=52.0,
        source="Wikipedia: Rudolph the Red-Nosed Reindeer (TV special).",
    ),
    NumericTiebreaker(
        question="What is the runtime of the original 1947 Miracle on 34th Street, "
        "in minutes?",
        answer=96.0,
        source="Wikipedia: Miracle on 34th Street.",
    ),
    NumericTiebreaker(
        question="What was Love Actually's worldwide box office total, in millions "
        "of dollars?",
        answer=252.6,
        source="Box Office Mojo, corroborated by Wikipedia: Love Actually - lifetime "
        "total as of 2026, includes a 2023 holiday re-release.",
    ),
    NumericTiebreaker(
        question="What was the production budget of The Nightmare Before Christmas, "
        "in millions of dollars?",
        answer=18.0,
        source="Wikipedia: The Nightmare Before Christmas.",
    ),
    # --- Music ---
    NumericTiebreaker(
        question="In what year was Mariah Carey's 'All I Want for Christmas Is You' "
        "originally released?",
        answer=1994.0,
        source="Wikipedia: All I Want for Christmas Is You.",
    ),
    NumericTiebreaker(
        question="Despite being released in 1994, that same Mariah Carey song "
        "didn't hit number one on the Billboard Hot 100 until decades later. In "
        "what year did it first top the chart?",
        answer=2019.0,
        source="Billboard, 'Mariah Carey's \"All I Want for Christmas Is You\" Hits "
        "No. 1 on Billboard Hot 100' - chart dated December 21, 2019.",
    ),
    NumericTiebreaker(
        question="According to Guinness World Records, how many copies has Bing "
        "Crosby's 1942 recording of 'White Christmas' sold worldwide, in millions?",
        answer=50.0,
        source="Guinness World Records, via Wikipedia: White Christmas (song).",
    ),
    NumericTiebreaker(
        question="In what year did James Lord Pierpont copyright 'Jingle Bells,' "
        "under its original title 'The One Horse Open Sleigh'?",
        answer=1857.0,
        source="Wikipedia: Jingle Bells.",
    ),
    NumericTiebreaker(
        question="If you add up every single gift given across all twelve verses "
        "of 'The Twelve Days of Christmas,' counting every repeat, what is the "
        "grand total number of gifts?",
        answer=364.0,
        source="Song lyrics - sum of the first twelve triangular numbers "
        "(the twelfth tetrahedral number): 1+3+6+10+15+21+28+36+45+55+66+78 = 364.",
    ),
    NumericTiebreaker(
        question="In what year did Handel's 'Messiah' - source of the "
        "Christmas-season staple 'Hallelujah' chorus - premiere?",
        answer=1742.0,
        source="Wikipedia: Messiah (Handel) - premiered April 13, 1742, in Dublin.",
    ),
    NumericTiebreaker(
        question="Wham!'s 'Last Christmas' was famously denied the UK Christmas "
        "number one in 1984. In what year did it finally reach number one on the "
        "UK Singles Chart for the first time?",
        answer=2021.0,
        source="Wikipedia: Last Christmas - reached number one on the New Year's "
        "Day 2021 chart.",
    ),
    NumericTiebreaker(
        question="Gene Autry's 1949 recording of 'Rudolph the Red-Nosed Reindeer' "
        "has sold how many copies, in millions?",
        answer=12.5,
        source="Wikipedia: Rudolph, the Red-Nosed Reindeer (song).",
    ),
    NumericTiebreaker(
        question="In what year was Andy Williams' 'It's the Most Wonderful Time of "
        "the Year' originally released?",
        answer=1963.0,
        source="Wikipedia: It's the Most Wonderful Time of the Year.",
    ),
    NumericTiebreaker(
        question="'O Holy Night' began life in France as 'Cantique de Noel.' In "
        "what year was it composed?",
        answer=1847.0,
        source="Wikipedia: O Holy Night.",
    ),
    NumericTiebreaker(
        question="Mel Torme said he and Bob Wells wrote 'The Christmas Song' "
        "('Chestnuts Roasting on an Open Fire') in how many minutes, on a hot July "
        "day in 1945?",
        answer=40.0,
        source="Wikipedia: The Christmas Song - per Mel Torme's own account.",
    ),
    NumericTiebreaker(
        question="'Carol of the Bells' began as the Ukrainian folk song 'Shchedryk.' "
        "In what year was it first performed, by a student choir in Kyiv?",
        answer=1916.0,
        source="Wikipedia: Shchedryk (song) - first performed December 19, 1916.",
    ),
    NumericTiebreaker(
        question="In what year was 'Santa Claus Is Comin' to Town,' written by "
        "Haven Gillespie and J. Fred Coots, written?",
        answer=1934.0,
        source="Wikipedia: Santa Claus Is Comin' to Town.",
    ),
    # --- Records & traditions ---
    NumericTiebreaker(
        question="What is the record height, in feet, of the tallest Christmas "
        "tree ever displayed at Rockefeller Center, a 10-ton Norway spruce shown "
        "in 1999?",
        answer=100.0,
        source="NBC New York, corroborated by CNN's December 2, 1999 coverage of "
        "that year's tree lighting.",
    ),
    NumericTiebreaker(
        question="In what year did the Rockefeller Center Christmas tree tradition "
        "begin, when construction workers pooled their money for a modest 20-foot "
        "balsam fir?",
        answer=1931.0,
        source="New-York Historical Society, 'The First Christmas Tree at "
        "Rockefeller Center.'",
    ),
    NumericTiebreaker(
        question="NORAD's 'Tracks Santa' tradition began because of a wrong number "
        "printed in a Sears ad. In what year did this start?",
        answer=1955.0,
        source="NORAD official fact sheet, 'NORAD Tracks Santa.'",
    ),
    NumericTiebreaker(
        question="The world's tallest artificial Christmas tree, erected in "
        "Colombo, Sri Lanka in 2016, stood how many feet tall, to the nearest foot?",
        answer=237.0,
        source="Guinness World Records, 'Largest artificial Christmas tree' "
        "(72.1 meters, approximately 237 feet).",
    ),
    NumericTiebreaker(
        question="A Norwegian collector holds the Guinness World Record for the "
        "largest collection of Christmas bauble ornaments, set in 2023. How many "
        "baubles did it contain?",
        answer=3101.0,
        source="Guinness World Records, 'Largest collection of Christmas bauble "
        "ornaments' (set August 4, 2023, in Fredrikstad, Norway).",
    ),
    NumericTiebreaker(
        question="When Charles Dickens published 'A Christmas Carol' in London in "
        "1843, how many copies made up that very first print run?",
        answer=6000.0,
        source="History Today, 'Publication of A Christmas Carol,' corroborated by "
        "Smithsonian Magazine.",
    ),
    NumericTiebreaker(
        question="According to the National Christmas Tree Association's 2023 "
        "survey, how many real, farm-grown Christmas trees were purchased in the "
        "US that year, in millions?",
        answer=21.58,
        source="National Christmas Tree Association, 2023 Consumer Survey.",
    ),
    NumericTiebreaker(
        question="'The Elf on the Shelf: A Christmas Tradition,' the book that "
        "launched the elf-watching craze, was first published in what year?",
        answer=2005.0,
        source="Wikipedia: The Elf on the Shelf.",
    ),
    NumericTiebreaker(
        question="Before Rudolph ever joined the team, how many reindeer pulled "
        "Santa's sleigh in the original 1823 poem 'A Visit from St. Nicholas'?",
        answer=8.0,
        source="Wikipedia: A Visit from St. Nicholas / Santa Claus's reindeer - "
        "Dasher, Dancer, Prancer, Vixen, Comet, Cupid, Donner, and Blitzen.",
    ),
    NumericTiebreaker(
        question="For its 2023 'Countdown to Christmas' season, Hallmark premiered "
        "a record number of brand-new original holiday movies across its two "
        "channels. How many total new movies premiered that season?",
        answer=40.0,
        source="Tom's Guide, 'Hallmark Christmas movies 2023 - when and where to "
        "stream all 40 movies.'",
    ),
    NumericTiebreaker(
        question="Rudolph the Red-Nosed Reindeer was invented as an advertising "
        "giveaway character for a department store, not part of Santa's original "
        "crew. In what year was Rudolph created?",
        answer=1939.0,
        source="Smithsonian National Postal Museum collection record, corroborated "
        "by Wikipedia: Rudolph the Red-Nosed Reindeer - created by Robert L. May "
        "for Montgomery Ward.",
    ),
    NumericTiebreaker(
        question="In what year was 'A Visit from St. Nicholas' - better known today "
        "as 'The Night Before Christmas' - first published anonymously in a small "
        "New York newspaper, the Troy Sentinel?",
        answer=1823.0,
        source="History.com, 'The poem \"A Visit From St. Nicholas\" is first "
        "published,' corroborated by Wikipedia.",
    ),
    # --- History & culture ---
    NumericTiebreaker(
        question="According to the most commonly cited tradition, in what year did "
        "Saint Nicholas of Myra - the historical bishop behind the Santa Claus "
        "legend - die?",
        answer=343.0,
        source="St. Nicholas Center, 'Who is St. Nicholas?,' corroborated by "
        "Wikipedia: Saint Nicholas.",
    ),
    NumericTiebreaker(
        question="On what day of December is St. Nicholas Day - the feast day tied "
        "to his traditional death date - celebrated?",
        answer=6.0,
        source="Encyclopedia Britannica, 'St. Nicholas Day.'",
    ),
    NumericTiebreaker(
        question="In what year did Coca-Cola first run advertisements featuring "
        "Haddon Sundblom's now-iconic plump, red-suited Santa Claus?",
        answer=1931.0,
        source="The Coca-Cola Company, 'Did Coca-Cola Create Santa Claus?'",
    ),
    NumericTiebreaker(
        question="In what year did the US Congress officially declare Christmas "
        "Day a federal holiday?",
        answer=1870.0,
        source="Wikipedia: Federal holidays in the United States.",
    ),
    NumericTiebreaker(
        question="In what year was the world's first commercially printed "
        "Christmas card - the one commissioned by Sir Henry Cole - sold?",
        answer=1843.0,
        source="Victoria and Albert Museum, 'The First Christmas Card.'",
    ),
    NumericTiebreaker(
        question="In what year did President Calvin Coolidge first light the "
        "National Christmas Tree in a public ceremony, starting an annual "
        "tradition that continues today?",
        answer=1923.0,
        source="White House Historical Association, 'A Coolidge Christmas,' "
        "corroborated by the National Park Service.",
    ),
    NumericTiebreaker(
        question="How many days apart are Western Christmas, on December 25th, and "
        "Orthodox Christmas, on January 7th - a gap caused by the difference "
        "between the Gregorian and Julian calendars?",
        answer=13.0,
        source="St. Michael Antiochian Orthodox Christian Church, 'What is the "
        "Julian calendar vs. the Gregorian calendar?'",
    ),
    NumericTiebreaker(
        question="How many days make up the traditional 'Twelve Days of Christmas,' "
        "running from December 25th through January 5th?",
        answer=12.0,
        source="Wikipedia: Twelve Days of Christmas.",
    ),
    NumericTiebreaker(
        question="How many candles are traditionally placed around an Advent "
        "wreath, one lit for each Sunday of Advent?",
        answer=4.0,
        source="Wikipedia: Advent wreath, corroborated by U.S. Catholic.",
    ),
    NumericTiebreaker(
        question="On what day of December is Krampusnacht - the night Krampus, St. "
        "Nicholas's horned companion, is said to roam the streets of Austria - "
        "traditionally observed?",
        answer=5.0,
        source="Wikipedia: Krampus.",
    ),
    NumericTiebreaker(
        question="In what year did Boxing Day become an official bank holiday in "
        "England, Wales, and Ireland, under an act of Parliament?",
        answer=1871.0,
        source="Wikipedia: Bank Holidays Act 1871.",
    ),
)
