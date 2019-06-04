#!/usr/bin/python3
# -*- coding: utf-8 -*-
""" This python script is written for the NPL challenge for June 2019. The goal is to login to three routers, read in
	the output of a 'show ip route' command from each router. Then analyze it and show a summary of how many
	routes are:
		Connected
		EIGRP
		Local
		OSPF
		Static
	
	I used netmiko to log into the routers and issue the 'show ip route' command. I then used the textfsm module to 
	analyze the route info. I also used a pre-built template from the ntc-templates project
	which was already setup to analyze 'show ip route' output. I included the template in this script if the file is not present.
	
	The user will be prompted for the username and password to use for logging into the devices.
	
	Arguments:
		None
	
	Outputs:
		1) Text with the analysis of the show ip route commands from each device
"""

# import modules HERE

# import the standard Python modules
import sys											# this allows us to analyze the arguments	
import os											# this allows us to check on the file
from datetime import datetime						# useful for getting timing information and for some data translation from Excel files
from contextlib import contextmanager
import getpass											# for username and password information
import logging											# log output, issues, etc
import time
import tempfile

# import any extras
try:
	import textfsm									# output formatter
except:
	print("Need to have textfsm installed. Try:\n  pip<version> install textfsm")
	sys.exit()
try:
	import xlrd											# this allows us to import an Excel file
	import xlwt											# this allows us to output data to an Excel file
	from xlutils.copy import copy as excel_copy_rdwt	# this allows a workbook read in to be converted to a workbook that can be written
except:
	print("Excel packages need to be installed. Try:\n  pip<version> install xlrd\n  pip<version> install xlwt\n  pip<version> install xlutils")
	sys.exit()
try:
	from netmiko import ConnectHandler					# this will be used to establish SSH connections with devices, send commands, and retrieve output
except:
	print("Need to have netmiko installed. Try:\n  pip<version> install netmiko")
	sys.exit()
	
# additional information about the script
__filename__ = "getAndAnalyzeIPRoute.py"
__author__ = "Robert Hallinan"
__email__ = "rhallinan@netcraftsmen.com"

#
# version history
#

"""
	20190603 - Initial version
"""

@contextmanager
def open_file(path, mode):
	the_file = open(path, mode)
	yield the_file
	the_file.close()

def loggerSetup(fileName,loggerName):
	# setup the logger to log with the file name provided (directory would be logs/<fileName>
	# potential levels are: Critical Error Warning Info Debug

	logFile = 'logs' + os.sep + fileName

	# check to make sure that the logs directory exists
	if not os.path.isdir('logs'):
		try:
			os.mkdir('logs')
		except:
			print("Can't make the directory. Exiting...")
			sys.exit()		

	# Create a custom logger
	logger = logging.getLogger(loggerName)
	logger.setLevel(logging.DEBUG)

	# Create handlers
	c_handler = logging.StreamHandler()
	f_handler = logging.FileHandler(logFile)
	c_handler.setLevel(logging.CRITICAL)
	f_handler.setLevel(logging.DEBUG)

	# Create formatters and add it to handlers
	c_format = logging.Formatter('%(name)s - %(levelname)s - %(message)s')
	f_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
	c_handler.setFormatter(c_format)
	f_handler.setFormatter(f_format)

	# Add handlers to the logger
	logger.addHandler(c_handler)
	logger.addHandler(f_handler)

	return logger

def getUserInfo():

	# let's get the username from the user:
	loginConf = "N"
	while loginConf != "Y":
		loginName = input("Please enter the username you wish to use for logins into devices: ")
		loginConf = input("You have entered the following username: \"" + loginName + "\". Is this correct (Y/N)? ")
		loginConf = loginConf.upper()

	print("The following username will be used for logins: " + loginName)

	# let's get the password from the user
	passPrompt=getpass
	userPass=""
	userPassConf=""
	while (userPass == "" and userPassConf == "") or (userPass != userPassConf):			# i.e. check for the initial condition where both are empty or if they don't match
		userPass = passPrompt.getpass()
		userPassConf = passPrompt.getpass("Reenter the password: ")
		if userPass != userPassConf:
			print("Passwords do not match. Please try again....")
	
	return loginName,userPass


def build_iproute_template():
	""" 
	This is the information for the show ip route template. It comes directly from:
		https://github.com/networktocode/ntc-templates/blob/master/templates/cisco_ios_show_ip_route.template
	This script will make this file.	
	"""

	fileContents = [
		'Value Filldown PROTOCOL (\w)\n', 
		'Value Filldown TYPE (\w{0,2})\n', 
		'Value Required,Filldown NETWORK (\d{1,3}.\d{1,3}.\d{1,3}.\d{1,3})\n', 
		'Value Filldown MASK (\d{1,2})\n', 
		'Value DISTANCE (\d+)\n', 
		'Value METRIC (\d+)\n', 
		'Value NEXTHOP_IP (\d{1,3}.\d{1,3}.\d{1,3}.\d{1,3})\n', 
		'Value NEXTHOP_IF ([A-Z][\w\-\.:/]+)\n', 
		'Value UPTIME (\d[\w:\.]+)\n', 
		'\n', 
		'Start\n', 
		'  ^Gateway.* -> Routes\n', 
		'\n', 
		'Routes\n', 
		'  # For "is (variably )subnetted" line, capture mask, clear all values.\n', 
		'  ^\s+\d{1,3}.\d{1,3}.\d{1,3}.\d{1,3}\/${MASK}\sis -> Clear\n', 
		'  #\n', 
		'  # Match directly connected route with explicit mask\n', 
		'  ^${PROTOCOL}(\s|\*)${TYPE}\s+${NETWORK}\/${MASK}\sis\sdirectly\sconnected,\s${NEXTHOP_IF} -> Record\n', 
		'  #\n', 
		'  # Match directly connected route (mask is inherited from "is subnetted")\n', 
		'  ^${PROTOCOL}(\s|\*)${TYPE}\s+${NETWORK}\sis\sdirectly\sconnected,\s${NEXTHOP_IF} -> Record\n', 
		'  #\n',
		'  # Match regular routes, with mask, where all data in same line\n',
		'  ^${PROTOCOL}(\s|\*)${TYPE}\s+${NETWORK}\/${MASK}\s\[${DISTANCE}/${METRIC}\]\svia\s${NEXTHOP_IP}(,\s${UPTIME})?(,\s${NEXTHOP_IF})? -> Record\n',
		'  #\n',
		'  # Match regular route, all one line, where mask is learned from "is subnetted" line\n',
		'  ^${PROTOCOL}(\s|\*)${TYPE}\s+${NETWORK}\s\[${DISTANCE}\/${METRIC}\]\svia\s${NEXTHOP_IP}(,\s${UPTIME})?(,\s${NEXTHOP_IF})? -> Record\n',
		'  #\n',
		'  # Match route with no via statement (Null via protocol)\n',
		'  ^${PROTOCOL}(\s|\*)${TYPE}\s+${NETWORK}\/${MASK}\s\[${DISTANCE}/${METRIC}\],\s${UPTIME},\s${NEXTHOP_IF} -> Record\n',
		'  #\n',
		'  # Match "is a summary" routes (often Null0)\n',
		'  ^${PROTOCOL}(\s|\*)${TYPE}\s+${NETWORK}\/${MASK}\sis\sa\ssummary,\s${UPTIME},\s${NEXTHOP_IF} -> Record\n',
		'  #\n',
		'  # Match regular routes where the network/mask is on the line above the rest of the route\n',
		'  ^${PROTOCOL}(\s|\*)${TYPE}\s+${NETWORK}\/${MASK} -> Next\n',
		'  #\n',
		'  # Match regular routes where the network only (mask from subnetted line) is on the line above the rest of the route\n',
		'  ^${PROTOCOL}(\s|\*)${TYPE}\s+${NETWORK} -> Next\n',
		'  #\n',
		'  # Match the rest of the route information on line below network (and possibly mask)\n',
		'  ^\s+\[${DISTANCE}\/${METRIC}\]\svia\s${NEXTHOP_IP}(,\s${UPTIME})?(,\s${NEXTHOP_IF})? -> Record\n',
		'  #\n',
		'  # Match load-balanced routes\n',
		'  ^\s+\[${DISTANCE}\/${METRIC}\]\svia\s${NEXTHOP_IP} -> Record\n',
		'  #\n',
		'  # Clear all variables on empty lines\n',
		'  ^\s* -> Clearall\n',
		'\n',
		'EOF\n',	
	]
	with open_file('cisco_ios_show_ip_route.template','w') as fileOut:
		fileOut.writelines(fileContents)

def outputExcel(listOutput,fileName,tabName):
	""" listOutput: this should be a list of lists; first item should be header file which should be written.
		fileName: Name of the Excel file to which this data should be written
		tabName: Name of the tab to which this data should be written
	"""

	# since before this would get called - it is assumed that the file was initialized - if the file now exists it is because another
	# tab is already in it from this script - thus check to see if the file is there - if so then just open workbook using xlrd
	if os.path.exists(fileName):
		outBook = xlrd.open_workbook(fileName)
		outBookNew = excel_copy_rdwt(outBook)
		outBook = outBookNew
	else:	
		# make the new Workbook object
		outBook = xlwt.Workbook()

	# add the sheet with the tab name specified
	thisSheet = outBook.add_sheet(tabName)

	# get number of columns
	numCols=len(listOutput[0])

	for rowNum in range(len(listOutput)):
		writeRow = thisSheet.row(rowNum)
		# print(listOutput[rowNum])
		for x in range(numCols):
			writeRow.write(x,str(listOutput[rowNum][x]))
			
	# save it to the Excel sheet at the end
	outBook.save(fileName)

def establishSSHConnect(dstEndpoint,deviceType,userName,userPassword):

	# build the dictionary with the connection info for the device
	connectInfo={ 'device_type': deviceType,
				  'host': dstEndpoint,
				  'username': userName,
				  'password': userPassword,
				  'global_delay_factor': 2,
				}

	# connect to the device
	thisLogger.info("Trying to establish a SSH connection with: " + dstEndpoint + "....")
	try:
		new_connection = ConnectHandler(**connectInfo)
	except:
		thisLogger.critical("Can't connect with " + dstEndpoint + ". Skipping this device....")
		return False

	thisLogger.info("Successful connection with " + dstEndpoint)

	return new_connection

def main(system_arguments):

	#***********************************
	#*
	#* Initial global setups
	#*
	#***********************************
	global curTime
	curTime = datetime.utcnow().strftime("%Y%m%d%H%M")

	#***********************************
	#*
	#* Setup Logging
	#*
	#***********************************
	global thisLogger
	thisLogger = loggerSetup(curTime + '.log','analyzeIPRoute')	

	# test the logger
	# thisLogger.critical('This is a critical test')
	# thisLogger.error('This is an error test')
	# thisLogger.warning('This is a warning test')
	# thisLogger.info('This is an informational test')
	# thisLogger.debug('This is a debug test')

	# get user password info
	loginName, userPass = getUserInfo()	
	
	# define a list of the private IP addresses we should log in to
	ipAddressList = ['10.102.3.11', '10.102.3.12', '10.102.3.13']

	# build the template
	build_iproute_template()

	# loop through each device
	for deviceIP in ipAddressList:

		# build the SSH connection
		net_connect = establishSSHConnect(deviceIP, 'cisco_ios', loginName, userPass)
		if type(net_connect) == type(bool()):
			# this means a return of False which means that there was no connection made - so done with this device
			continue		

		# read in the template file
		with open('cisco_ios_show_ip_route.template','r') as fileIn:
			re_table = textfsm.TextFSM(fileIn)
			
		with tempfile.TemporaryFile('w+t') as shRoute:
			# execute the command on the device
			time.sleep(1)
			try:
				net_connect.clear_buffer()
				shRoute.writelines(net_connect.send_command('show ip route'))
			except Exception as e:
				thisLogger.exception(e)
			try:
				net_connect.disconnect()
			except Exception as e:
				thisLogger.exception(e)

			# read in the data - first seek to 0, then parse it
			shRoute.seek(0)
			routeInfo = re_table.ParseText(shRoute.read())

		# get a set of the unique protocol, network, and mask
		# protocol is field 0, network is field 2, mask is field 3
		uniqueRoutes = set()
		for eachItem in routeInfo:
			uniqueRoutes.add((eachItem[0],eachItem[2],eachItem[3]))

		# print out a report for the user
		print("\n" * 2)
		print("************************************************")
		print("*                                              *")
		print("*       Route Summary for " + deviceIP + "          *")
		print("*                                              *")
		print("************************************************")
		print()
		print("The following device's ip route table was analyzed: " + deviceIP)
		print()
		print("The number of connected routes is: " + str(len([ x for x in uniqueRoutes if x[0]=="C" ])))
		print("The number of EIGRP routes is: " + str(len([ x for x in uniqueRoutes if x[0]=="D" ])))
		print("The number of Local routes is: " + str(len([ x for x in uniqueRoutes if x[0]=="L" ])))
		print("The number of OSPF routes is: " + str(len([ x for x in uniqueRoutes if x[0]=="O" ])))
		print("The number of static routes is: " + str(len([ x for x in uniqueRoutes if x[0]=="S" ])))
		print("\n" * 1)

	# delete the file that I added
	try:
		os.remove('cisco_ios_show_ip_route.template')
	except:
		pass

if __name__ == "__main__":

	# this gets run if the script is called by itself from the command line
	main(sys.argv)