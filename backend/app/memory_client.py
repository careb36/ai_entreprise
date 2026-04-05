import json
import numpy as np
from typing import List, Dict, Any, Optional
from database import get_db_connection, get_neo4j_driver

class MemoryClient:
    """Client for Postgres/pgvector and Neo4j memory operations."""

    def __init__(self, pg_conn=None, neo4j_driver=None):
        self.pg_conn = pg_conn or get_db_connection()
        self.neo4j_driver = neo4j_driver or get_neo4j_driver()    
    def store(self, content: str, metadata: Optional[Dict] = None):
        """Store content in pgvector and Neo4j."""
        from llm_proxy_client import LLMProxyClient
        client = LLMProxyClient()
        embedding = client.embed(content)
        
        if embedding:
            cursor = self.pg_conn.cursor()
            cursor.execute(
                """
                INSERT INTO vector_memory (content, embedding, metadata)
                VALUES (%s, %s, %s)
                """,
                (content, json.dumps(embedding), json.dumps(metadata or {}))
            )
            self.pg_conn.commit()
            cursor.close()
        
        with self.neo4j_driver.session() as session:
            session.run(
                """
                CREATE (m:Memory {
                    content: $content,
                    metadata: $metadata,
                    created: timestamp()
                })
                """,
                content=content, metadata=json.dumps(metadata or {})
            )
    
    def search_similar(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search similar content using pgvector."""
        from llm_proxy_client import LLMProxyClient
        client = LLMProxyClient()
        embedding = client.embed(query)
        
        if not embedding:
            return []
        
        cursor = self.pg_conn.cursor()
        cursor.execute(
            """
            SELECT content, metadata, 1 - (embedding <=> %s::vector) as similarity
            FROM vector_memory
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (json.dumps(embedding), json.dumps(embedding), limit)
        )
        
        results = []
        for row in cursor.fetchall():
            results.append({
                "content": row[0],
                "metadata": row[1],
                "similarity": row[2]
            })
        
        cursor.close()
        return results
    
    def store_entity(self, entity_type: str, properties: Dict[str, Any]):
        """Store entity in Neo4j."""
        with self.neo4j_driver.session() as session:
            session.run(
                f"""
                CREATE (e:{entity_type} $props)
                """,
                props=properties
            )
    
    def query_graph(self, cypher: str, **kwargs) -> List[Dict[str, Any]]:
        """Execute Cypher query on Neo4j."""
        with self.neo4j_driver.session() as session:
            result = session.run(cypher, **kwargs)
            return [dict(record) for record in result]
    
    def get_relationships(self, node_type: str, node_id: str, rel_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get relationships for a node."""
        if rel_type:
            cypher = f"""
            MATCH (n:{node_type} {{id: $id}})-[r:{rel_type}]->(m)
            RETURN n, r, m
            """
        else:
            cypher = f"""
            MATCH (n:{node_type} {{id: $id}})-[r]->(m)
            RETURN n, r, m
            """
        
        with self.neo4j_driver.session() as session:
            result = session.run(cypher, id=node_id)
            return [dict(record) for record in result]
