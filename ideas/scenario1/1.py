# I'm restricting myself here to Pydantic models and functions.
#
# Retrieve all stocks beneath x price.
# For each - get its average volume for the last 5 days - store it's value in a hash map.
# Write to json dict.

import requests
import os
import pydantic
import queue
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

# Should globals even be allowed? I like their convenience personally. These could be script arguments as well.
MIN_PRICE = 400
TIME_SPAN_DAYS = 5
WORKERS = 5
CLIENT_ID = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["SECRET_ID"]
BASE_URL = "https://stocktrader.io"


class Quote(pydantic.BaseModel):
    id: str # NVDA
    price: float # 145.12
    volume: int # 2219892
    quote_timestamp: int # 1750125915408


def get_session() -> requests.Session:
    query_params = {
        "client_id":CLIENT_ID,
        "client_secret":CLIENT_SECRET,
        "grant_type":"client_credentials",
        "scope":"default"
    }
    access_token_response = requests.post(f"{BASE_URL}/authorize", params=query_params)
    access_token_response.raise_for_status()
    logging.info("Authorization successful.")
    
    access_token = access_token_response.json()["access_token"]

    new_session = requests.Session
    new_session.headers["Authorization"] = f"Bearer {access_token}" 
    # We can add exponential backoff here in the case of throttling.

    return new_session


def get_quotes(page_key: str | None, session: requests.Session) -> tuple[list, str]:
    query_params = {
        "filter" : f"price>={MIN_PRICE}"
    }
    if page_key:
        query_params["pageKey"] = page_key
    response = session.get(f"{BASE_URL}/quotes", params=query_params)
    response.raise_for_status()

    return response["data"], response["pageKey"]


def stock_quote_producer(queue: queue.Queue[Quote], session: requests.Session):
    page_key = None
    quotes, page_key = get_quotes(page_key=page_key, session=session)
    for quote in quotes: 
        queue.put(Quote(*quote))
    
    while page_key != None: # Could use while True here to de-duplicate, but I like the explicit condition.
        quotes, page_key = get_quotes(page_key=page_key, session=session)
        for quote in quotes: 
            queue.put(Quote(*quote))


def stock_quote_consumer(quote_queue: queue.Queue[Quote], session: requests.Session, stocks: dict):
    query_params = {
        "range" : 60*60*24*TIME_SPAN_DAYS
    }
    while not quote_queue.empty:
        try:
            current_quote = quote_queue.get(block=True, timeout=10)
        except queue.Empty as e:
            logging.info(f"Couldn't find an object after 10 seconds - consumer exiting...")
            break
        response = session.get(f"{BASE_URL}/quotes/{current_quote.id}", params=query_params)
        response.raise_for_status() # Let's assume we don't have to paginate here.

        quotes = response.json()["data"]
        quotes = [Quote(*quote) for quote in quotes] # We wanna validate it before any manipulation.
        total_volume = sum(quote.volume for quote in quotes)
        average_volume = total_volume / len(quotes) # Quotes should NEVER be of len 0.
        stocks[current_quote.id] = average_volume

        quote_queue.task_done()
    

def main():
    logging.info("Starting application...")
    session = get_session()
    quote_queue = queue.Queue() # Mutable reference.
    stocks = {}

    logging.info("Tabulating data...")
    futures = []
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures.append(executor.submit(stock_quote_producer, quote_queue, session))
        futures += [executor.submit(stock_quote_consumer, quote_queue, session, stocks) for i in range(WORKERS-1)]
        for future in as_completed(futures):
             print(f"Result: {future.result()}")

    cwd = os.getcwd()
    logging.info(f"Done! File written to : {cwd}/output.json")
    with open("output.json") as f:
        f.write(json.dumps(stocks, indent=4))


if __name__ == "__main__":
    main()