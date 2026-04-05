import json
from typing import List, Dict, Any, Optional
from sqlmodel import Session, text
from database import engine, get_neo4j_driver

class MemoryClient:
    """Client for Postgres/pgvector and Neo4j memory operations using SQLModel engine."""

    def __init__(self, neo4j_driver=None):
        self.engine = engine
        self.neo4j_driver = neo4j_driver or get_neo4j_driver()    

    def store(self, content: str, metadata: Optional[Dict] = None):
        """Store content in pgvector and Neo4j."""
        from llm_proxy_client import LLMProxyClient
        client = LLMProxyClient()
        embedding = client.embed(content)
        
        if embedding:
            with Session(self.engine) as session:
                session.execute(
                    text("""
                        INSERT INTO vector_memory (content, embedding, metadata)
                        VALUES (:content, :embedding, :metadata)
                    """),
                    {
                        "content": content,
                        "embedding": json.dumps(embedding),
                        "metadata": json.dumps(metadata or {})
                    }
                )
                session.commit()
        
        with self.neo4j_driver.session() as neo_session:
            neo_session.run(
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
        """Search similar content using pgvector via SQLModel engine."""
        from llm_proxy_client import LLMProxyClient
        client = LLMProxyClient()
        embedding = client.embed(query)
        
        if not embedding:
            return []
        
        with Session(self.engine) as session:
            # Note: The embedding parameter is cast to vector inside the SQL
            results_proxy = session.execute(
                text("""
                    SELECT content, metadata, 1 - (embedding <=> :embedding::vector) as similarity
                    FROM vector_memory
                    ORDER BY embedding <=> :embedding::vector
                    LIMIT :limit
                """),
                {
                    "embedding": json.dumps(embedding),
                    "limit": limit
                }
            )
            
            results = []
            for row in results_proxy:
                results.append({
                    "content": row[0],
                    "metadata": row[1] if isinstance(row[1], dict) else json.loads(row[1] or "{}"),
                    "similarity": row[2]
                })
            
            return results
    
    def store_entity(self, entity_type: str, properties: Dict[str, Any]):
        """Store entity in Neo4j."""
        with self.neo4j_driver.session() as neo_session:
            neo_session.run(
                f"CREATE (e:{entity_type} $props)",
                props=properties
            )
    
    def query_graph(self, cypher: str, **kwargs) -> List[Dict[str, Any]]:
        """Execute Cypher query on Neo4j."""
        with self.neo4j_driver.session() as neo_session:
            result = neo_session.run(cypher, **kwargs)
            return [dict(record) for record in result]
    
    def get_relationships(self, node_type: str, node_id: str, rel_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get relationships for a node."""
        if rel_type:
            cypher = f"MATCH (n:{node_type} {{id: $id}})-[r:{rel_type}]->(m) RETURN n, r, m"
        else:
            cypher = f"MATCH (n:{node_type} {{id: $id}})-[r]->(m) RETURN n, r, m"
        
        with self.neo4j_driver.session() as neo_session:
            result = neo_session.run(cypher, id=node_id)
            return [dict(record) for record in result]
