-- Kurgusal bir sipariş kaydını yerel test veritabanında güncelleme örneği.
UPDATE order_records
SET customer_email = 'musteri@example.com',
    purchase_date = '2026-01-01',
    delivery_date = '2026-01-05'
WHERE order_id = 'ORNEK-001';
