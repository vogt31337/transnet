import logging

from Util import Util


class InferenceValidator:
    cur = None

    def __init__(self, cur):
        self.cur = cur

    def validate(self, ssid, circuits, boundary, voltage_levels):
        num_stations = InferenceValidator.num_stations(circuits)
        logging.info('In total %d stations covered with the inference', num_stations)
        sql = "SELECT DISTINCT(unnest(get_stations(r.parts))) FROM planet_osm_rels r, planet_osm_polygon s1"
        sql += ", planet_osm_polygon s2" if boundary else ""
        sql += ''' where s1.osm_id = %s
            and s1.power ~ 'substation|station|sub_station'
            and s1.voltage ~ '%s' and ARRAY[s1.osm_id]::bigint[] <@ r.parts''' \
               % (str(ssid), voltage_levels)
        if boundary:
            sql += ''' and (s2.power ~ 'substation|station|sub_station'
                and s2.voltage ~ '220000|380000'
                or s2.power ~ 'generator|plant')
                and ARRAY[s2.osm_id]::bigint[] <@ r.parts
                and st_within(s2.way, st_transform(st_geomfromtext('%s',4269),3857))''' % boundary.wkt
        self.cur.execute(sql)
        result = self.cur.fetchall()
        if not result:
            logging.info('No existing relation found for station %s', str(ssid))
            return None
        not_hit_stations = []
        hits = 0
        for (station,) in result:
            station_hit = False
            for circuit in circuits:
                if station == circuit.members[0].id or station == circuit.members[-1].id:
                    hits += 1
                    station_hit = True
                    break
            if not station_hit:
                not_hit_stations.append(station)
        logging.info('Found %d of %d connected stations to %s', hits, len(result), str(ssid))
        logging.info('Not hit stations: %s', str(not_hit_stations))
        return hits * 1.0 / len(result)

    def validate2(self, circuits, stations_dict, boundary, voltage_levels):

        logging.info("Starting inference validation")

        sql = '''SELECT DISTINCT r.id AS osm_id,
                  r.parts AS parts,
                  hstore(r.tags)->'voltage' AS voltage
                  FROM planet_osm_rels r, planet_osm_polygon s1,
                  planet_osm_polygon s2 '''

        sql += '''where ((s1.power ~ 'substation|station|sub_station'
                and s1.voltage ~ '%s') or s1.power ~ 'generator|plant')
                and ARRAY[s1.osm_id]::bigint[] <@ r.parts
                and st_within(s1.way, st_transform(st_geomfromtext('%s',4269),3857)) ''' \
               % (voltage_levels, boundary.wkt)

        sql += '''and ((s2.power ~ 'substation|station|sub_station'
                and s2.voltage ~ '%s')
                or s2.power ~ 'generator|plant')
                and ARRAY[s2.osm_id]::bigint[] <@ r.parts
                and st_within(s2.way, st_transform(st_geomfromtext('%s',4269),3857)) ''' % \
               (voltage_levels, boundary.wkt)

        sql += '''and s1.osm_id <> s2.osm_id
            and hstore(r.tags)->'route'='power' '''

        self.cur.execute(sql)
        relations_result = self.cur.fetchall()

        num_eligible_relations = len(relations_result)

        hits = 0
        not_hit_connections = []
        not_hit_connection_percentage = []
        length_of_eligible_relation = 0
        length_of_covered_eligible_relation = 0
        length_of_inferred_relation_covering = 0

        voltages = sorted([int(v) for v in voltage_levels.split('|')])

        covered_connections = []
        filtered_circuits = []
        for (_id, parts, voltage) in relations_result:
            if not voltage or int(voltage) < voltages[0]:
                sql = "SELECT parts FROM planet_osm_rels WHERE id = " + str(_id)
                self.cur.execute(sql)
                result2 = self.cur.fetchall()
                for (parts2,) in result2:
                    for part in parts2:
                        sql = "SELECT hstore(tags)->'voltage' FROM planet_osm_ways WHERE id = " + str(part)
                        self.cur.execute(sql)
                        result3 = self.cur.fetchall()
                        if not result3:
                            voltage = None
                            continue
                        [(part_voltage,)] = result3
                        if not part_voltage:
                            voltage = None
                            continue
                        if ';' not in part_voltage and ',' not in part_voltage and int(part_voltage) >= voltages[0]:
                            voltage = part_voltage
                            break

            if not voltage:
                logging.debug("Could not determine voltage of relation")
                num_eligible_relations -= 1
                continue

            sql_stations_ids = '''SELECT array_agg(s.osm_id) AS stations_ids
                FROM planet_osm_polygon s
                WHERE ARRAY [s.osm_id] :: INTEGER [] <@ %s AND
                ((s.power ~ 'substation|station|sub_station' AND s.voltage ~ %s) OR
                 s.power ~ 'plant|generator')'''

            self.cur.execute(sql_stations_ids, [parts, voltage_levels])
            station_ids = self.cur.fetchone()[0]
            if len(station_ids) > 1:
                station1 = station_ids[0]
                station2 = station_ids[1]
                if str(station1) + str(station2) + str(voltage) in covered_connections \
                        or str(station2) + str(station1) + str(voltage) in covered_connections:
                    continue
                covered_connections.append(str(station1) + str(station2) + str(voltage))
                filtered_circuits.append({
                    'station_ids': station_ids,
                    'voltage': voltage,
                    'osm_id': _id,
                    'parts': parts
                })

        for filtered_circuit in filtered_circuits:
            relation_covered = False
            num_hit_p2p_connections = 0
            covered_stations = dict()

            sql_lines = '''SELECT l.osm_id AS osm_id,
                            l.voltage AS voltages,
                            st_length(st_transform(l.way, 4326), TRUE) AS spheric_length
                            FROM planet_osm_line l
                            WHERE ARRAY [l.osm_id] :: INTEGER [] <@ %s 
                            AND  l.power ~ 'line|cable|minor_line' AND l.voltage ~ %s '''

            self.cur.execute(sql_lines, [filtered_circuit['parts'], voltage_levels])
            circuit_lines = self.cur.fetchall()

            length_of_manual_relation = 0
            for (line_id, voltages, length,) in circuit_lines:
                length_of_manual_relation += length

            length_of_eligible_relation += length_of_manual_relation

            for circuit in circuits:
                if Util.have_common_voltage(circuit.voltage, filtered_circuit['voltage']):
                    station1 = circuit.members[0]
                    station1_connected_stations = InferenceValidator.find_connected_stations(
                        stations_dict, filtered_circuit['voltage'],
                        station1.connected_stations[filtered_circuit['voltage']],
                        {station1.id})
                    index1 = 0
                    index2 = index1 + 1
                    while index2 < len(filtered_circuit['station_ids']):
                        pairs = '{0}-{1}'.format(filtered_circuit['station_ids'][index1],
                                                 filtered_circuit['station_ids'][index2])
                        if pairs not in covered_stations and (
                                        filtered_circuit['station_ids'][index1] in station1_connected_stations and
                                        filtered_circuit['station_ids'][index2] in station1_connected_stations):
                            num_hit_p2p_connections += 1
                            covered_stations[pairs] = pairs
                            for line in circuit.members[1:-1]:
                                length_of_inferred_relation_covering += line.length
                        index1 += 1
                        index2 = index1 + 1

                if num_hit_p2p_connections == len(filtered_circuit['station_ids']) - 1:
                    relation_covered = True
                    break
            if relation_covered:
                hits += 1
                length_of_covered_eligible_relation += length_of_manual_relation
            else:
                if len(station_ids) - 1 > 0:
                    not_hit_connection_percentage.append(num_hit_p2p_connections / (len(station_ids) - 1))
                    # logging.info(InferenceValidator.to_overpass_string(filtered_circuit['parts']))
                not_hit_connections.append(filtered_circuit['osm_id'])

        if num_eligible_relations:
            hit_rate = hits * 1.0 / num_eligible_relations
        else:
            hit_rate = 1
        logging.info('Found %d of %d eligible point-to-point connections (%.2lf)', hits, num_eligible_relations,
                     hit_rate)
        # logging.info('Not hit point-to-point connections: %s', str(not_hit_connections))
        if len(not_hit_connections):
            logging.info('Not hit point-to-point connections percentage average: %s',
                         str(sum(not_hit_connection_percentage) / len(not_hit_connections)))
        logging.info('Number of all found relations %d ' % len(circuits))
        logging.info('Number of all eligible OSM relations %d ' % num_eligible_relations)
        logging.info('Length of eligible relations %d ' % round(length_of_eligible_relation / 1000))
        logging.info(
            'length of covered eligible relations %d ' % round(length_of_covered_eligible_relation / 1000))
        logging.info('Length of inferred relations covering %d ' % round(length_of_inferred_relation_covering / 1000))

    @staticmethod
    def find_connected_stations(stations, voltage, connected_stations, covered_stations):
        for station_id in connected_stations.difference(covered_stations):
            covered_stations.add(station_id)
            connected_stations.update(
                InferenceValidator.find_connected_stations(stations, voltage,
                                                           stations[station_id].connected_stations[voltage],
                                                           covered_stations))
        return connected_stations

    @staticmethod
    def num_stations(circuits):
        stations = set()
        stations.clear()
        for circuit in circuits:
            stations.add(circuit.members[0])
            stations.add(circuit.members[-1])
        return len(stations)

    @staticmethod
    def to_overpass_string(parts):
        overpass = ''
        for part in parts:
            overpass += 'way(' + str(part) + ');(._;>;);out;'
        return overpass
