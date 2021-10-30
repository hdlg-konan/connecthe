# -*- coding: utf-8 -*-
# Copyright (c) 2015, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""
# Integrating CSB
### Validate Currency
Example:
	from frappe.integrations.utils import get_payment_gateway_controller
	controller = get_payment_gateway_controller("CSB")
	controller().validate_transaction_currency(currency)
### 2. Redirect for payment
Example:
	payment_details = {
		"amount": 600,
		"title": "Payment for bill : 111",
		"description": "payment via cart",
		"reference_doctype": "Payment Request",
		"reference_docname": "PR0001",
		"payer_email": "NuranVerkleij@example.com",
		"payer_name": "Nuran Verkleij",
		"order_id": "111",
		"currency": "INR",
		"payment_gateway": "CSB",
		"subscription_details": {
			"plan_id": "plan_12313", # if Required
			"start_date": "2018-08-30",
			"billing_period": "Month" #(Day, Week, Month, Year),
			"billing_frequency": 1,
			"customer_notify": 1,
			"upfront_amount": 1000
		}
	}
	# Redirect the user to this url
	url = controller().get_payment_url(**payment_details)
### 3. On Completion of Payment
Write a method for `on_payment_authorized` in the reference doctype
Example:
	def on_payment_authorized(payment_status):
		# this method will be called when payment is complete
##### Notes:
payment_status - payment gateway will put payment status on callback.
For CSB payment status is Authorized
"""

import frappe
from frappe import _
import json
import connecthe
import hmac
import CSB
import hashlib
import requests, base64
from urllib.parse import urlencode
from frappe.model.document import Document
from frappe.utils import get_url, call_hook_method, cint, get_timestamp
from frappe.integrations.utils import (make_get_request, make_post_request, create_request_log,
	create_payment_gateway)

class CSBSettings(Document):
	supported_currencies = ["XPF"]

	def init_client(self):
		if self.api_key:
            secret = self.get_password(fieldname="api_secret", raise_exception=False)
            usrPass = "self.api_key:secret"
            b64Val = base64.b64encode(usrPass)
           
			
	def validate(self):
		create_payment_gateway('CSB')
		call_hook_method('payment_gateway_enabled', gateway='CSB')
		if not self.flags.ignore_mandatory:
			self.validate_CSB_credentails()

	def validate_CSB_credentails(self):
		if self.api_key and self.api_secret:
			try:
                secret = self.get_password(fieldname="api_secret", raise_exception=False)
                usrPass = "self.api_key:secret"
                b64Val = base64.b64encode(usrPass)
                api_URL = "https://epaync.nc/api-payment/V4/Charge/SDKTest"
				requests.post(api_URL,headers={"Authorization": "Basic %s" % b64Val},data=payload) 
			except Exception:
				frappe.throw(_("Seems API Key or API Secret is wrong !!!"))

	def validate_transaction_currency(self, currency):
		if currency not in self.supported_currencies:
			frappe.throw(_("Please select another payment method. CSB does not support transactions in currency '{0}'").format(currency))

	def setup_addon(self, settings, **kwargs):
		"""
			Addon template:
			{
				"item": {
					"name": row.upgrade_type,
					"amount": row.amount,
					"currency": currency,
					"description": "add-on description"
				},
				"quantity": 1 (The total amount is calculated as item.amount * quantity)
			}
		"""
		url = "https://epaync.nc/api-payment/V4/Charge/SDKTest".format(kwargs.get('subscription_id'))

		try:
			if not frappe.conf.converted_rupee_to_paisa:
				convert_rupee_to_paisa(**kwargs)

			for addon in kwargs.get("addons"):
				resp = make_post_request(
					url,
					auth=(settings.api_key, settings.api_secret),
					data=json.dumps(addon),
					headers={
						"content-type": "application/json"
					}
				)
				if not resp.get('id'):
					frappe.log_error(str(resp), 'CSB Failed while creating subscription')
		except:
			frappe.log_error(frappe.get_traceback())
			# failed
			pass

	
	def get_payment_url(self, **kwargs):
		integration_request = create_request_log(kwargs, "Host", "CSB")
		return get_url("./integrations/CSB_checkout?token={0}".format(integration_request.name))

	def create_order(self, **kwargs):
		# Creating Orders https://CSB.com/docs/api/orders/

		# convert rupees to paisa
		kwargs['amount'] *= 100

		# Create integration log
		integration_request = create_request_log(kwargs, "Host", "CSB")

		# Setup payment options
		payment_options = {
			"amount": kwargs.get('amount'),
			"currency": kwargs.get('currency', 'INR'),
			"receipt": kwargs.get('receipt'),
			"payment_capture": kwargs.get('payment_capture')
		}
		if self.api_key and self.api_secret:
			try:
				order = make_post_request("https://api.CSB.com/v1/orders",
					auth=(self.api_key, self.get_password(fieldname="api_secret", raise_exception=False)),
					data=payment_options)
				order['integration_request'] = integration_request.name
				return order # Order returned to be consumed by CSB.js
			except Exception:
				frappe.log(frappe.get_traceback())
				frappe.throw(_("Could not create CSB order"))

	def create_request(self, data):
		self.data = frappe._dict(data)

		try:
			self.integration_request = frappe.get_doc("Integration Request", self.data.token)
			self.integration_request.update_status(self.data, 'Queued')
			return self.authorize_payment()

		except Exception:
			frappe.log_error(frappe.get_traceback())
			return{
				"redirect_to": frappe.redirect_to_message(_('Server Error'), _("Seems issue with server's CSB config. Don't worry, in case of failure amount will get refunded to your account.")),
				"status": 401
			}

	def authorize_payment(self):
		"""
		An authorization is performed when user’s payment details are successfully authenticated by the bank.
		The money is deducted from the customer’s account, but will not be transferred to the merchant’s account
		until it is explicitly captured by merchant.
		"""
		data = json.loads(self.integration_request.data)
		settings = self.get_settings(data)

		try:
			resp = make_get_request("https://api.CSB.com/v1/payments/{0}"
				.format(self.data.CSB_payment_id), auth=(settings.api_key,
					settings.api_secret))

			if resp.get("status") == "authorized":
				self.integration_request.update_status(data, 'Authorized')
				self.flags.status_changed_to = "Authorized"

			if resp.get("status") == "captured":
				self.integration_request.update_status(data, 'Completed')
				self.flags.status_changed_to = "Completed"

			elif data.get('subscription_id'):
				if resp.get("status") == "refunded":
					# if subscription start date is in future then
					# CSB refunds the amount after authorizing the card details
					# thus changing status to Verified

					self.integration_request.update_status(data, 'Completed')
					self.flags.status_changed_to = "Verified"

			else:
				frappe.log_error(str(resp), 'CSB Payment not authorized')

		except:
			frappe.log_error(frappe.get_traceback())
			# failed
			pass

		status = frappe.flags.integration_request.status_code

		redirect_to = data.get('redirect_to') or None
		redirect_message = data.get('redirect_message') or None
		if self.flags.status_changed_to in ("Authorized", "Verified", "Completed"):
			if self.data.reference_doctype and self.data.reference_docname:
				custom_redirect_to = None
				try:
					frappe.flags.data = data
					custom_redirect_to = frappe.get_doc(self.data.reference_doctype,
						self.data.reference_docname).run_method("on_payment_authorized", self.flags.status_changed_to)

				except Exception:
					frappe.log_error(frappe.get_traceback())

				if custom_redirect_to:
					redirect_to = custom_redirect_to

			redirect_url = 'payment-success?doctype={0}&docname={1}'.format(self.data.reference_doctype, self.data.reference_docname)
		else:
			redirect_url = 'payment-failed'

		if redirect_to:
			redirect_url += '&' + urlencode({'redirect_to': redirect_to})
		if redirect_message:
			redirect_url += '&' + urlencode({'redirect_message': redirect_message})

		return {
			"redirect_to": redirect_url,
			"status": status
		}

	def get_settings(self, data):
		settings = frappe._dict({
			"api_key": self.api_key,
			"api_secret": self.get_password(fieldname="api_secret", raise_exception=False)
		})

		if cint(data.get('notes', {}).get('use_sandbox')) or data.get("use_sandbox"):
			settings.update({
				"api_key": frappe.conf.sandbox_api_key,
				"api_secret": frappe.conf.sandbox_api_secret,
			})

		return settings

	def cancel_subscription(self, subscription_id):
		settings = self.get_settings({})

		try:
			resp = make_post_request("https://api.CSB.com/v1/subscriptions/{0}/cancel"
				.format(subscription_id), auth=(settings.api_key,
					settings.api_secret))
		except Exception:
			frappe.log_error(frappe.get_traceback())

	def verify_signature(self, body, signature, key):
		key = bytes(key, 'utf-8')
		body = bytes(body, 'utf-8')

		dig = hmac.new(key=key, msg=body, digestmod=hashlib.sha256)

		generated_signature = dig.hexdigest()
		result = hmac.compare_digest(generated_signature, signature)

		if not result:
			frappe.throw(_('CSB Signature Verification Failed'), exc=frappe.PermissionError)

		return result

def capture_payment(is_sandbox=False, sanbox_response=None):
	"""
		Verifies the purchase as complete by the merchant.
		After capture, the amount is transferred to the merchant within T+3 days
		where T is the day on which payment is captured.
		Note: Attempting to capture a payment whose status is not authorized will produce an error.
	"""
	controller = frappe.get_doc("CSB Settings")

	for doc in frappe.get_all("Integration Request", filters={"status": "Authorized",
		"integration_request_service": "CSB"}, fields=["name", "data"]):
		try:
			if is_sandbox:
				resp = sanbox_response
			else:
				data = json.loads(doc.data)
				settings = controller.get_settings(data)

				resp = make_get_request("https://api.CSB.com/v1/payments/{0}".format(data.get("CSB_payment_id")),
					auth=(settings.api_key, settings.api_secret), data={"amount": data.get("amount")})

				if resp.get('status') == "authorized":
					resp = make_post_request("https://api.CSB.com/v1/payments/{0}/capture".format(data.get("CSB_payment_id")),
						auth=(settings.api_key, settings.api_secret), data={"amount": data.get("amount")})

			if resp.get("status") == "captured":
				frappe.db.set_value("Integration Request", doc.name, "status", "Completed")

		except Exception:
			doc = frappe.get_doc("Integration Request", doc.name)
			doc.status = "Failed"
			doc.error = frappe.get_traceback()
			doc.save()
			frappe.log_error(doc.error, '{0} Failed'.format(doc.name))


@frappe.whitelist(allow_guest=True)
def get_api_key():
	controller = frappe.get_doc("CSB Settings")
	return controller.api_key

@frappe.whitelist(allow_guest=True)
def get_order(doctype, docname):
	# Order returned to be consumed by CSB.js
	doc = frappe.get_doc(doctype, docname)
	try:
		# Do not use run_method here as it fails silently
		return doc.get_CSB_order()
	except AttributeError:
		frappe.log_error(frappe.get_traceback(), _("Controller method get_CSB_order missing"))
		frappe.throw(_("Could not create CSB order. Please contact Administrator"))

@frappe.whitelist(allow_guest=True)
def order_payment_success(integration_request, params):
	"""Called by CSB.js on order payment success, the params
	contains CSB_payment_id, CSB_order_id, CSB_signature
	that is updated in the data field of integration request
	Args:
		integration_request (string): Name for integration request doc
		params (string): Params to be updated for integration request.
	"""
	params = json.loads(params)
	integration = frappe.get_doc("Integration Request", integration_request)

	# Update integration request
	integration.update_status(params, integration.status)
	integration.reload()

	data = json.loads(integration.data)
	controller = frappe.get_doc("CSB Settings")

	# Update payment and integration data for payment controller object
	controller.integration_request = integration
	controller.data = frappe._dict(data)

	# Authorize payment
	controller.authorize_payment()

@frappe.whitelist(allow_guest=True)
def order_payment_failure(integration_request, params):
	"""Called by CSB.js on failure
	Args:
		integration_request (TYPE): Description
		params (TYPE): error data to be updated
	"""
	frappe.log_error(params, 'CSB Payment Failure')
	params = json.loads(params)
	integration = frappe.get_doc("Integration Request", integration_request)
	integration.update_status(params, integration.status)

def convert_rupee_to_paisa(**kwargs):
	for addon in kwargs.get('addons'):
		addon['item']['amount'] *= 100

	frappe.conf.converted_rupee_to_paisa = True

@frappe.whitelist(allow_guest=True)
def CSB_subscription_callback():
	try:
		data = frappe.local.form_dict

		validate_payment_callback(data)

		data.update({
			"payment_gateway": "CSB"
		})

		doc = frappe.get_doc({
			"data": json.dumps(frappe.local.form_dict),
			"doctype": "Integration Request",
			"integration_type": "Subscription Notification",
			"status": "Queued"
		}).insert(ignore_permissions=True)
		frappe.db.commit()

		frappe.enqueue(method='frappe.integrations.doctype.CSB_settings.CSB_settings.handle_subscription_notification',
			queue='long', timeout=600, is_async=True, **{"doctype": "Integration Request", "docname":  doc.name})

	except frappe.InvalidStatusError:
		pass
	except Exception as e:
		frappe.log(frappe.log_error(title=e))

def validate_payment_callback(data):
	def _throw():
		frappe.throw(_("Invalid Subscription"), exc=frappe.InvalidStatusError)

	subscription_id = data.get('payload').get("subscription").get("entity").get("id")

	if not(subscription_id):
		_throw()

	controller = frappe.get_doc("CSB Settings")

	settings = controller.get_settings(data)

	resp = make_get_request("https://api.CSB.com/v1/subscriptions/{0}".format(subscription_id),
		auth=(settings.api_key, settings.api_secret))

	if resp.get("status") != "active":
		_throw()

def handle_subscription_notification(doctype, docname):
	call_hook_method("handle_subscription_notification", doctype=doctype, docname=docname)