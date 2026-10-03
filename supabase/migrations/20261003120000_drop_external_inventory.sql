-- Elimina las tablas de inventario externo (POIs y carreteras) que solo
-- alimentaba la sincronización con la API del reto, ya retirada.
DROP TABLE IF EXISTS public.external_pois_inventory;
DROP TABLE IF EXISTS public.external_roads_inventory;
