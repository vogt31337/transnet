"""							         
Copyright "2015" "NEXT ENERGY"						  
										  
Licensed under the Apache License, Version 2.0 (the "License");		  
you may not use this file except in compliance with the License.	  
You may obtain a copy of the License at					  
										  
http://www.apache.org/licenses/LICENSE-2.0				  

Unless required by applicable law or agreed to in writing, software	  
distributed under the License is distributed on an "AS IS" BASIS,	  
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.  
See the License for the specific language governing permissions and	  
limitations under the License.
"""

import psycopg2
from optparse import OptionParser
from Line import Line
from Station import Station

class Transnet:

    def __init__(self, database, user, host, port, password):
        # Initializes the SciGRID class with the database connection parameters.
        # These parameters are: database name, database user, database password, database host and port. 
        # Notice: The password will not be stored.

        self.connection = {'database':database, 'user':user, 'host':host, 'port':port}
        self.connect_to_DB(password)

    def get_connection_data(self):
	# Obtain the database connection parameters. 
        return self.connection
    
    def connect_to_DB(self, password):   
	# Establish the database connection. 
        self.conn = psycopg2.connect(password=password, **self.connection)
        self.cur = self.conn.cursor()

    def reconnect_to_DB(self):
	# Reconnect to the database if connection got lost. 
        msg = "Please enter the database password for \n\t database=%s, user=%s, host=%s, port=%port \nto reconnect to the database: " \
            %(str(self.connection['database']), str(self.connection['user']), str(self.connection['host']), str(self.connection['port'])) 
        password = raw_input(msg)
        self.connect_to_DB(self, password)

    def create_relations(self):
        stations = dict()
        sql = "select id,hstore(tags)->'name' as name, hstore(tags)->'power' as type, nodes,tags from planet_osm_ways where hstore(tags)->'power'~'station|substation|sub_station|plant|generator' and array_length(nodes, 1) >= 4 and st_isclosed(create_line(id))"
        self.cur.execute(sql)
        result = self.cur.fetchall()
        for (id,name,type,nodes,tags) in result:
            stations[id] = Station(id,type,name,nodes,tags)
            # print(str(stations[id]) + '\n')

        #for id in stations:
        close_stations = self.get_close_stations(137197826, stations)
        circuits = []
        circuits.extend(self.infer_circuits(stations[137197826],close_stations))

        i = 1
        for circuit in circuits:
            print('Circuit ' + str(i))
            print(self.print_circuit(circuit))
            i+=1
        return

    def infer_circuits(self, station, stations):
        circuits = []
        sql =   """
                select id, hstore(tags)->'voltage' as voltage, hstore(tags)->'power' as type, hstore(tags)->'cables' as cables, hstore(tags)->'name' as name, hstore(tags)->'ref' as ref, nodes, tags
                from planet_osm_ways where hstore(tags)->'power'~'line|cable|minor_line' and exist(hstore(tags),'voltage')
	                    and id in (select osm_id from planet_osm_line where st_intersects(create_polygon(""" + str(station.id) + """), way));
                """
        self.cur.execute(sql)
        result = self.cur.fetchall()
        for (id,voltage,type,cables,name,ref,nodes,tags) in result:
            line = Line(id,type,voltage,cables,name,ref,nodes,tags)
            print(str(line) + '\n')
            circuit = [station, line]
            node_to_continue = line.first_node()
            temp_stations = dict()
            temp_stations[station.id] = station
            if self.node_in_substation(line.first_node(), temp_stations):
                node_to_continue = line.last_node()
            circuits.append(self.infer_circuit(circuit,node_to_continue,line,voltage,ref,cables,stations))
        return circuits

    # recursive function that infers electricity circuits
    # circuit - sorted member array
    # line - line of circuit
    # stations - all known stations
    def infer_circuit(self, circuit, node_id, from_line, circuit_voltage, circuit_ref, prev_cables, stations):
        station_id = self.node_in_substation(node_id, stations)
        if station_id and station_id != circuit[0].id:
            circuit.append(stations[station_id])
            return circuit

        sql =   """
                select id, hstore(tags)->'voltage' as voltage, hstore(tags)->'power' as type, hstore(tags)->'cables' as cables,hstore(tags)->'name' as name, hstore(tags)->'ref' as ref, nodes, tags
                from planet_osm_ways where hstore(tags)->'power'~'line|cable|minor_line' and exist(hstore(tags),'voltage')
	                    and nodes::bigint[] @> ARRAY[""" + str(node_id) + """]::bigint[];
                """
        self.cur.execute(sql)
        result = self.cur.fetchall()
        for (id,voltage,type,cables,name,ref,nodes,tags) in result:
            line = Line(id,type,voltage,cables,name,ref,nodes,tags)
            if line.id == from_line.id:
                continue
            if circuit_voltage not in voltage:
                continue
            if not self.ref_matches(circuit_ref, ref):
                continue
            circuit.append(line)
            node_to_continue = line.first_node()
            if line.first_node() == node_id:
                node_to_continue = line.last_node()
            return self.infer_circuit(circuit, node_to_continue, line, circuit_voltage, circuit_ref, cables, stations)

        print('Could not obtain circuit ' + self.print_circuit(circuit))
        return circuit

    # returns if node is in station
    def node_in_substation(self, node_id, stations):
        for id in stations:
            station = stations[id]
            if node_id in station.nodes:
                # node is a part of a substation
                return station.id
            sql = " select true from planet_osm_ways where id = " + str(station.id) + " and st_intersects(create_polygon(id), create_point(" + str(node_id) + "));"
            self.cur.execute(sql)
            result = self.cur.fetchall()
            if result:
                # node is within a substation
                return station.id
        return None

    def num_subs_in_circuit(self, circuit):
        num_stations = 0
        for way in circuit:
            if isinstance(way, Station):
                num_stations+=1
        return num_stations

    def print_circuit(self, circuit):
        string = ''
        overpass = ''
        for way in circuit:
            string += str(way) + ','
            overpass += 'way(' + str(way.id) + ');'
        return string + overpass

    # compares the ref/name tokens like 303;304 in the power line tags
    def ref_matches(self, ref1, ref2):
        if ref1 is None or ref2 is None:
            # this should not be a necessary criteria - only use it when specified
            return True
        split_char_1 = ';'
        if ',' in ref1:
            split_char_1 = ','
        split_char_2 = ';'
        if ',' in ref2:
            split_char_2 = ','
        for token1 in ref1.split(split_char_1):
            for token2 in ref2.split(split_char_2):
                if token1.strip() == token2.strip():
                    return True
        return False

    def get_close_stations(self, station_id, stations):
        close_stations = dict()
        stations_clause = list('ARRAY[')
        for station_id in stations:
            stations_clause.extend(str(station_id))
            stations_clause.append(',')
        stations_clause[len(stations_clause) - 1] = ']'
        sql = "select id from planet_osm_ways where ARRAY[id]::bigint[] <@ " + "".join(stations_clause) + "::bigint[] and st_distance(st_centroid(create_polygon(id)), st_centroid(create_polygon(" + str(station_id) + "))) <= 300000"
        self.cur.execute(sql)
        result = self.cur.fetchall()
        for id, in result:
            print(id)
            close_stations[id] = stations[id]
        return stations
    
if __name__ == '__main__':
    
    parser=OptionParser()
    parser.add_option("-D","--dbname", action="store", dest="dbname", \
    help="database name of the topology network")
    parser.add_option("-H","--dbhost", action="store", dest="dbhost", \
    help="database host address of the topology network")
    parser.add_option("-P","--dbport", action="store", dest="dbport", \
    help="database port of the topology network")
    parser.add_option("-U","--dbuser", action="store", dest="dbuser", \
    help="database user name of the topology network")
    parser.add_option("-X","--dbpwrd", action="store", dest="dbpwrd", \
    help="database user password of the topology network")
    
    (options, args) = parser.parse_args()
    # get connection data via command line or set to default values
    dbname = options.dbname if options.dbname else 'de_power_151125_de2'
    dbhost = options.dbhost if options.dbhost else '127.0.0.1'
    dbport = options.dbport if options.dbport else '5432'
    dbuser = options.dbuser if options.dbuser else 'postgres' 
    dbpwrd = options.dbpwrd if options.dbpwrd else 'open50arms'
 
    # Connect to DB 
    try:
        transnet_instance = Transnet(database=dbname, user=dbuser, port=dbport, host=dbhost, password=dbpwrd)
    except:
        print "Could not connect to database. Please check the values of host,port,user,password, and database name."
        parser.print_help()
        exit() 

    transnet_instance.create_relations()


    
    
