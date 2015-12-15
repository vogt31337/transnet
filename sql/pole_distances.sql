﻿select id, voltage, distance from (select id, hstore(tags)->'voltage' as voltage, avg_distance_between_poles(id) as distance from planet_osm_ways where hstore(tags)->'power'~'line' and hstore(tags)->'voltage' ~ E'^[0,1,2,3,4,5,6,7,8,9]+$' and 
                  ((hstore(tags)->'voltage')::integer = 110000 or (hstore(tags)->'voltage')::integer = 220000 or (hstore(tags)->'voltage')::integer = 380000 or (hstore(tags)->'voltage')::integer = 400000)) m where distance is not null and distance <= 1000