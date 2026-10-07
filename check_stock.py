from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

import json
import os
import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "state.json"

products = [
	{
		"name": "Essential Slim Ribbed Henley",
		"url": "https://www.abercrombie.com/shop/ca/p/essential-slim-rib-henley-63404820",
		"wanted_colours": "non-striped"
	},
	{
		"name": "Essential Slim Ribbed Tee",
		"url": "https://www.abercrombie.com/shop/ca/p/essential-slim-ribbed-tee-63291319",
		"wanted_colours": ["black", "white"]
	}
]

def variant_key(variant):
	return "|".join([
		variant["product"].strip().lower(),
		variant["colour"].strip().lower(),
		variant["size"].strip().lower(),
		variant["length"].strip().lower(),
	])

def load_state():
	if not os.path.exists(STATE_FILE):
		return []

	with open(STATE_FILE, "r") as f:
		return json.load(f)


def save_state(available_variants):
	with open(STATE_FILE, "w") as f:
		json.dump(available_variants, f, indent=2)

def load_product_page(driver, wait, url):
	driver.get(url)

	try:
		wait.until(EC.presence_of_element_located((By.CLASS_NAME, "swatch-group__tiles")))
	except TimeoutException:
		print("Product page did not load, retrying...")
		driver.refresh()
		wait.until(EC.presence_of_element_located((By.CLASS_NAME, "swatch-group__tiles")))

def is_unavailable(element):
	wrapper = element.find_element(By.XPATH, "..")
	return wrapper.get_attribute("data-variant") == "unavailable"

def dismiss_cookie_popup(driver):
	try:
		accept_button = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler")))
		accept_button.click()

		WebDriverWait(driver, 5).until(EC.invisibility_of_element_located((By.CLASS_NAME, "onetrust-pc-dark-filter")))
		print("Bypassed cookie cosent popup")
	
	except TimeoutException:
		pass

def send_email(restocked_variants):
	load_dotenv(BASE_DIR / ".env")

	EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
	EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")
	EMAIL_TO = os.getenv("EMAIL_TO")

	if not EMAIL_ADDRESS or not EMAIL_APP_PASSWORD or not EMAIL_TO:
		raise RuntimeError("Email environment variables are not configured")

	if not restocked_variants:
		return

	msg = EmailMessage()
	msg["Subject"] = "Abercrombie Tall Restock Alert"
	msg["From"] = EMAIL_ADDRESS
	msg["To"] = EMAIL_TO
	
	body = "The following items are back in stock:\n\n"

	for variant in restocked_variants:
		body += (
			f"{variant['product']}\n"
			f"{variant['colour']} / {variant['size']} / {variant['length']}\n"
			f"{variant['url']}\n\n"
		)

	msg.set_content(body)

	with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
		smtp.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
		smtp.send_message(msg)

def check_product(driver, wait, product):

	available_variants = []

	print(f"Loading product: {product['name']}", flush=True)
	print(f"URL: {product['url']}", flush=True)

	driver.get(product["url"])
	print("Page load returned", flush=True)

	dismiss_cookie_popup(driver)

	print(f"Title: {driver.title}", flush=True)
	print(f"Current URL: {driver.current_url}", flush=True)

	colour_wrapper = wait.until(
		EC.presence_of_element_located((By.CLASS_NAME, "swatch-group__tiles"))
	)
	print("Found colour swatches", flush=True)

	colour_elements = colour_wrapper.find_elements(By.CLASS_NAME, "ds-swatch-tile")

	for parent in colour_elements:
		img_element = parent.find_element(By.XPATH, ".//img")
		colour = img_element.get_attribute("alt")
		colour_selector = parent.find_element(By.XPATH, ".//input")

		if product["wanted_colours"] == "non-striped":
			if "stripe" in colour.lower():
				continue
		else:
			if colour.lower() not in product["wanted_colours"]:
				continue

		print(f"Checking colour: {colour}", flush=True)

		colour_selector.click()
		wait.until(lambda d: colour_selector.is_selected())

		l_input = wait.until(EC.presence_of_element_located((By.ID, "pdp_radio_size_primary_L")))

		if is_unavailable(l_input):
			print(f"{colour}: Large unavailable", flush=True)
			continue

		# Explicitly select Large so Tall availability is calculated for L
		l_input.click()

		wait.until(lambda d: d.find_element(By.ID, "pdp_radio_size_primary_L").is_selected())

		# Re-find Tall after selecting Large in case the DOM was updated
		t_input = wait.until(EC.presence_of_element_located((By.ID, "pdp_radio_size_secondary_Tall")))

		if is_unavailable(t_input):
			print(f"{colour}: L Tall unavailable", flush=True)
		else:
			print(f"{colour}: L Tall AVAILABLE", flush=True)
			available_variants.append({
				"product": product["name"],
				"colour": colour,
				"size": "L",
				"length": "Tall",
				"url": product["url"]
			})

	return available_variants


def main():
	all_available = []

	for product in products:
		chrome_options = Options()

		# GitHub Actions / Linux runner stability settings.
		# Do not enable headless mode because Abercrombie blocked it locally.
		chrome_options.add_argument("--no-sandbox")
		chrome_options.add_argument("--disable-dev-shm-usage")

		print(f"\nStarting browser for: {product['name']}", flush=True)

		driver = None
		try:
			print("Creating Chrome driver...", flush=True)
			driver = webdriver.Chrome(options=chrome_options)
			driver.set_page_load_timeout(30)
			print("Chrome driver created", flush=True)

			wait = WebDriverWait(driver, 10)
			all_available.extend(check_product(driver, wait, product))

		except TimeoutException as e:
			print(f"Timed out while checking {product['name']}: {e}", flush=True)
			raise

		except WebDriverException as e:
			print(f"Selenium/Chrome error while checking {product['name']}: {e}", flush=True)
			raise

		finally:
			if driver is not None:
				print(f"Closing browser for: {product['name']}", flush=True)
				driver.quit()

	current_state = {variant_key(variant): variant for variant in all_available}
	previous_state = load_state()

	new_restocked = [variant for key, variant in current_state.items() if key not in previous_state]

	print(f"Available variants found: {len(all_available)}", flush=True)
	print(f"New restocks found: {len(new_restocked)}", flush=True)

	send_email(new_restocked)
	save_state(current_state)

	print(all_available, flush=True)


main()