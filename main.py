"""Unified entry point: python main.py reddot|if [crawler arguments]."""
import argparse

import reddot_crawler as crawler
import if_crawler


class RedDotCrawler:
    """Independent Red Dot runner; construction does not access the network."""

    def run(self, argv=None):
        args = crawler.build_parser().parse_args(argv)
        return crawler.run(args)


class IFStudentCrawler:
    """Experimental iF runner; live coverage remains unverified."""

    def run(self, argv=None):
        args = if_crawler.build_parser().parse_args(argv)
        return if_crawler.fetch(args)


def run_reddot(argv=None):
    return RedDotCrawler().run(argv)


def run_if(argv=None):
    return IFStudentCrawler().run(argv)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('award', choices=['reddot', 'if'])
    parser.add_argument('crawler_args', nargs=argparse.REMAINDER,
                        help='Arguments passed to the selected crawler; use fetch or discover')
    args = parser.parse_args(argv)
    runners = {'reddot': run_reddot, 'if': run_if}
    return runners[args.award](args.crawler_args)


if __name__ == '__main__':
    main()
