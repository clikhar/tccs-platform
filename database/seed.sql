INSERT INTO sections (code, name)
VALUES ('SEC-01', 'SECTION 01')
ON CONFLICT (code) DO NOTHING;

INSERT INTO controllers (code, name, section_id)
SELECT 'C01', 'Controller 01', id FROM sections WHERE code = 'SEC-01'
ON CONFLICT (code) DO NOTHING;

INSERT INTO stations (station_number, name, location, section_id, sip_extension, station_type, priority)
SELECT v.station_number, v.name, v.location, s.id, v.sip_extension, 'WAY_STATION', v.priority
FROM (VALUES
 ('101','AJNI CABIN','AJNI','1001',10),
 ('102','KAMPTEE','KAMPTEE','1002',20),
 ('103','MARAMJHIRI','MARAMJHIRI','1003',30),
 ('104','ITARSI','ITARSI','1004',40),
 ('105','DHARAKHOH','DHARAKHOH','1005',50),
 ('106','WAY STATION 106','SECTION 01','1006',60),
 ('107','WAY STATION 107','SECTION 01','1007',70),
 ('108','WAY STATION 108','SECTION 01','1008',80),
 ('109','WAY STATION 109','SECTION 01','1009',90),
 ('110','WAY STATION 110','SECTION 01','1010',100)
) AS v(station_number,name,location,sip_extension,priority)
CROSS JOIN (SELECT id FROM sections WHERE code = 'SEC-01') s
ON CONFLICT (station_number) DO NOTHING;

INSERT INTO station_groups (code, name, section_id)
SELECT 'SEC01-ALL', 'Section 01 All Stations', id
FROM sections WHERE code = 'SEC-01'
ON CONFLICT (code) DO NOTHING;

INSERT INTO station_group_members (group_id, station_id)
SELECT g.id, st.id
FROM station_groups g
CROSS JOIN stations st
WHERE g.code = 'SEC01-ALL'
  AND st.section_id = (SELECT id FROM sections WHERE code = 'SEC-01')
ON CONFLICT DO NOTHING;
