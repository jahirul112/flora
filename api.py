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

# tsol libs
from solc import compile_source, compile_standard
from jinja2 import Environment
from jinja2.nodes import Name
from io import BytesIO

DB_NAME = 'sqlite:///test.db'

# potential abstraction of engine to support sql, ipfs, yada yada
class Engine:
	def __init__(self, *args):
		raise NotImplementedError()
	def exists(self, query):
		raise NotImplementedError()
	def check_name(self, name):
		raise NotImplementedError()
	def add_name(self, name, n, e):
		raise NotImplementedError()
	def get_package(self, owner, package):
		raise NotImplementedError()
	def check_package(self, owner, package):
		raise NotImplementedError()
	def get_key(self, name):
		raise NotImplementedError()
	def set_secret(self, name, secret):
		raise NotImplementedError()
	def get_secret(self, name):
		raise NotImplementedError()
	def add_package(self, owner, package, template, example):
		raise NotImplementedError()

class IPFS_Engine(Engine):
	def __init__(self, *args):
		# info = ('127.0.0.1', 5000)
		# (IP add str, port int)
		try:
			self.api = ipfsapi.connect(args[0], args[1])
			self.root_hash = args[2]
			self.root_dir = args[3]
		except:
			raise Exception('Daemon not running')

		self.add_dir(os.path.join(os.getcwd(), self.root_dir))

	def add_dir(self, path):
		try:
			hashes = self.api.add(path, recursive=True)
			end_hash = hashes[-1]
			self.root_hash = end_hash['Hash']
			print(self.root_hash)
			return end_hash['Hash']
		except:
			# fails if nothing is in the directory
			return False

	def exists(self, query):
		if query == None:
			return False
		return True

	def check_name(self, name):
		try:
			query = self.api.ls('{}/packages/{}'.format(self.root_hash, name))
			return True
		except:
			return False

	def add_name(self, name, n, e):
		package_root = os.path.join(os.getcwd(), '{}/packages'.format(self.root_dir))
		package_path = (os.path.join(package_root, name))
		try:
			os.makedirs(package_path)
		except:
			pass

		name_root = os.path.join(os.getcwd(), '{}/names'.format(self.root_dir))
		name_path = (os.path.join(name_root, name))
		try:
			os.makedirs(name_path)
		except:
			pass

		with open(os.path.join(name_path, 'n'), 'w') as n_file:
			n_file.write(n)

		with open(os.path.join(name_path, 'e'), 'w') as e_file:
			e_file.write(e)

	def file_to_memory(self, path):
		data = None
		with open(path, 'r') as d:
			data = d.read()
		os.remove(path)
		return data

	def get_package(self, owner, package):
		# this will download locally or wherever the user is
		try:
			# pull it down from ipfs
			self.api.get('{}/packages/{}/{}'.format(self.root_hash, owner, package))

			# stores in cwdir, so load the files into memory and delete them
			template = self.file_to_memory('{}/{}/template'.format(os.getcwd(), package))
			example = self.file_to_memory('{}/{}/example'.format(os.getcwd(), package))
			os.rmdir('{}/{}'.format(os.getcwd(), package))

			return {
				'template' : template,
				'example' : example
			}
		except:
			return False

	def check_package(self, owner, package):
		try:
			query = self.api.ls('{}/packages/{}/{}'.format(self.root_hash, owner, package))
			return True
		except:
			return False
	
	def get_key(self, name):
		#try:
		# pull it down from ipfs
		self.api.get('{}/names/{}'.format(self.root_hash, name))

		# stores in cwdir, so load the files into memory and delete them
		n = self.file_to_memory('{}/{}/n'.format(os.getcwd(), name))
		e = self.file_to_memory('{}/{}/e'.format(os.getcwd(), name))

		os.rmdir('{}/{}'.format(os.getcwd(), name))

		return (n, e)
		# except:
		# 	return False
	
	def set_secret(self, name, secret):
		raise NotImplementedError()
	
	def get_secret(self, name):
		raise NotImplementedError()
	
	def add_package(self, owner, package, template, example):
		raise NotImplementedError()

class SQL_Engine(Engine):
	def __init__(self, *args):
		self.engine = create_engine(args[0])
		self.connection = self.engine.connect()

	def exists(self, query):
		if query == None:
			return False
		return True

	def check_name(self, name):
		query = self.connection.execute("SELECT * FROM names WHERE name='{}'".format(name)).fetchone()
		return self.exists(query)

	def add_name(self, name, n, e):
		query = self.connection.execute('INSERT INTO names VALUES (?,?,?,?)', (name, n, e, ''))
		return self.check_name(name)

	def get_package(self, owner, package):
		query = self.connection.execute("SELECT template, example FROM packages WHERE owner=? AND package=?", (owner, package)).fetchone()

		if query == None:
			return False

		return {
			'template' : pickle.loads(query[0]),
			'example' : pickle.loads(query[1])
		}

	def check_package(self, owner, package):
		query = self.connection.execute("SELECT * FROM packages WHERE owner='{}' AND package='{}'".format(owner, package)).fetchone()
		return self.exists(query)

	def get_key(self, name):
		return self.connection.execute("SELECT n, e FROM names WHERE name='{}'".format(name)).fetchone()

	def set_secret(self, name, secret):
		self.connection.execute("UPDATE names SET secret=? WHERE name=?", (secret, name))
		return self.exists(query)

	def get_secret(self, name):
		return self.connection("SELECT secret FROM names WHERE name='{}'".format(name)).fetchone()

	def add_package(self, owner, package, template, example):
		self.connection.execute('INSERT INTO packages VALUES (?,?,?,?)', (owner, package, template, example))
		return self.check_package(owner, package)

# copied directly from saffron contracts.py and slightly modified
# should be abstracted into its own tsol library eventually
def get_template_variables(fo):
	nodes = Environment().parse(fo.read()).body[0].nodes
	var_names = [x.name for x in nodes if type(x) is Name]
	return var_names

def render_contract(payload):
	sol_contract = payload.pop('sol')
	template_variables = get_template_variables(BytesIO(sol_contract.encode()))
	assert 'contract_name' in payload
	name = payload.get('contract_name')
	assert all(x in template_variables for x in list(payload.keys()))
	template = Environment().from_string(sol_contract)
	return name, template.render(payload)

def load_tsol_file(file=None, payload=None):
	assert file and payload, 'No file or payload provided.'
	payload['sol'] = file.read()
	name, rendered_contract = render_contract(payload=payload)
	return name, rendered_contract

input_json = '''{"language": "Solidity", "sources": {
				"{{name}}": {
					"content": {{sol}}
				}
			},
			"settings": {
				"outputSelection": {
					"*": {
						"*": [ "metadata", "evm.bytecode", "abi", "evm.bytecode.opcodes", "evm.gasEstimates", "evm.methodIdentifiers" ]
					}
				}
			}
		}'''

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
		payload = package_data['example']
		payload['sol'] = package_data['tsol']
		solidity = render_contract(payload)

		compilation_payload = Environment().from_string(input_json).render(name=solidity[0], sol=json.dumps(solidity[1]))

		# this will throw an assertation error (thanks piper!) if the code doesn't compile
		try:
			compile_standard(json.loads(compilation_payload))
		except:
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
