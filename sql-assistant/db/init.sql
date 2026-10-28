-- Small sample schema. Swap in Pagila later for a richer demo.
CREATE TABLE customers (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT UNIQUE NOT NULL,
  country TEXT,
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE products (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  price NUMERIC(10,2) NOT NULL
);

CREATE TABLE orders (
  id SERIAL PRIMARY KEY,
  customer_id INT REFERENCES customers(id),
  product_id INT REFERENCES products(id),
  quantity INT NOT NULL,
  ordered_at TIMESTAMP DEFAULT now()
);

INSERT INTO customers (name, email, country) VALUES
  ('Asha Verma', 'asha@example.com', 'India'),
  ('Liam Chen', 'liam@example.com', 'Singapore'),
  ('Sofia Rossi', 'sofia@example.com', 'Italy');
INSERT INTO products (name, price) VALUES
  ('Keyboard', 49.99), ('Monitor', 199.00), ('Mouse', 19.50);
INSERT INTO orders (customer_id, product_id, quantity) VALUES
  (1, 1, 2), (1, 3, 1), (2, 2, 1), (3, 1, 1), (3, 3, 4);

-- Read-only role: this is what the app uses to execute user queries
CREATE ROLE app_readonly LOGIN PASSWORD 'readonly_pw';
GRANT CONNECT ON DATABASE sampledb TO app_readonly;
GRANT USAGE ON SCHEMA public TO app_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO app_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO app_readonly;
