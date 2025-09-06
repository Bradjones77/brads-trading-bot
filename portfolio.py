# portfolio.py
from config import EMOJIS
import json
import os

PORTFOLIO_FILE = "portfolio.json"

# Initialize portfolio file
if not os.path.exists(PORTFOLIO_FILE):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump({}, f)

# Load portfolio
def load_portfolio():
    with open(PORTFOLIO_FILE, "r") as f:
        return json.load(f)

# Save portfolio
def save_portfolio(portfolio):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f)

# Add investment
def add_investment(coin, amount, buy_price):
    portfolio = load_portfolio()
    portfolio[coin] = {"amount": amount, "buy_price": buy_price}
    save_portfolio(portfolio)

# Update investment price
def update_investment(coin, new_price):
    portfolio = load_portfolio()
    if coin in portfolio:
        portfolio[coin]["buy_price"] = new_price
        save_portfolio(portfolio)

# Remove investment
def remove_investment(coin):
    portfolio = load_portfolio()
    if coin in portfolio:
        del portfolio[coin]
        save_portfolio(portfolio)

# Portfolio status
def portfolio_status():
    portfolio = load_portfolio()
    if not portfolio:
        return "📭 Your portfolio is empty."
    msg = "📊 *Your Portfolio:*\n"
    for coin, data in portfolio.items():
        amount = data["amount"]
        buy_price = data["buy_price"]
        msg += f"{coin}: {amount} @ {buy_price} USD\n"
    msg += "\n(Real-time P/L will be added with signal integration)"
    return msg
