import gevent
import gevent.monkey
gevent.monkey.patch_all()

from gevent.pywsgi import WSGIServer

from flask import Flask, request
from flask_restful import Resource, Api
from sqlalchemy import create_engine
import json
import os
import re
import click
import rsa
import string
import random
import pickle
from simplecrypt import encrypt, decrypt
import ipfsapi

import tsol

from engines import SQL_Engine, IPFS_Engine

DB_NAME = 'sqlite:///test.db'

# potential abstraction of engine to support sql, ipfs, yada yada
#api = ipfsapi.connect('127.0.0.1', 5001)

KEY = None

IPFS_LOCATION = ''

def error_payload(message):
	return {
		"status": "error",
		"data": None,
		"message": message
	}

def success_payload(data, message):
	return {
		"status": "success",
		"data": data,
		"message": message
	}

def clean(s):
	return re.sub('[^A-Za-z0-9]+', '', s)

def random_string(length):
    pool = string.ascii_letters + string.digits
    return ''.join(random.choice(pool) for i in range(length))

class NameRegistry(Resource):
	def get(self):
		sql = SQL_Engine(DB_NAME)

		if sql.check_name(request.form['name']) == True:
			return error_payload('Name already registered.')
		else:
			return success_payload(None, 'Name available to register.')

	def post(self):
		sql = SQL_Engine(DB_NAME)

		if sql.add_name(request.form['name'], request.form['n'], request.form['e']) == True:
			return success_payload(None, 'Name successfully registered.')
		else:
			return error_payload('Unavailable to register name.')

# GET does not require auth and just downloads packages. no data returns the DHT on IPFS or the whole SQL_Engine thing.
# POST required last secret. Secret is then flushed so auth is required again before POSTing again
class PackageRegistry(Resource):
	def get(self):
		# checks if the user can create a new package entry
		# if so, returns a new secret
		# user then must post the signed package to this endpoint
		sql = SQL_Engine(DB_NAME)

		if not sql.check_package(request.form['owner'], request.form['package']):
			# try to pull the users public key
			query = sql.get_key(request.form['owner'])

			# in doing so, check if the user exists
			if query == None:
				return error_payload('Owner does not exist.')

			# construct the user's public key
			user_public_key = rsa.PublicKey(int(query[0]), int(query[1]))

			# create a new secret
			secret = random_string(53)

			# sign and store it in the db so no plain text instance exists in the universe
			server_signed_secret = str(rsa.encrypt(secret.encode('utf8'), KEY[0]))
			query = sql.set_secret(owner, server_signed_secret)

			# sign and send secret to user
			user_signed_secret = rsa.encrypt(secret.encode('utf8'), user_public_key)
			return success_payload(str(user_signed_secret), 'Package available to register.')

		else:
			return error_payload('Package already exists.')

	def post(self):
		sql = SQL_Engine(DB_NAME)

		owner = request.form['owner']
		package = request.form['package']
		data = request.form['data']

		secret = rsa.decrypt(eval(sql.get_secret(owner)[0]), KEY[1])

		# data is a python tuple of the templated solidity at index 0 and an example payload at index 1
		# compilation of this code should return true
		# if there are errors, don't commit it to the db
		# otherwise, commit it
		raw_data = decrypt(secret, eval(data))
		package_data = json.loads(raw_data.decode('utf8'))
		'''
		payload = {
			'tsol' : open(code_path[0]).read(),
			'example' : example
		}
		'''

		# assert that the code compiles with the provided example
		if not tsol.does_compile(package_data['tsol'], package_data['example']):
			return error_payload('Provided payload contains compilation errors.')

		template = pickle.dumps(package_data['tsol'])
		example = pickle.dumps(package_data['example'])

		if sql.add_package(owner, package, template, example) == True:
			return success_payload(None, 'Package successfully uploaded.')
		return error_payload('Problem uploading package. Try again.')

class Packages(Resource):
	def get(self):
		sql = SQL_Engine(DB_NAME)

		data = sql.get_package(request.form['owner'], request.form['package'])

		if data == None:
			return error_payload('Could not find package.')

		return success_payload(data, 'Package successfully pulled.')

app = Flask(__name__)
api = Api(app)

api.add_resource(NameRegistry, '/names')
api.add_resource(PackageRegistry, '/package_registry')
api.add_resource(Packages, '/packages')

def main():
	(pub, priv) = rsa.newkeys(512)
	KEY = (pub, priv)
	print(KEY)
	http_server = WSGIServer(('', 5000), app)
	srv_greenlet = gevent.spawn(http_server.start)

if __name__ == '__main__':
	main()
